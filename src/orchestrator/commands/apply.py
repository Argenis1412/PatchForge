"""Apply the validated patch to the target repository."""

from __future__ import annotations

__all__ = [
    "execute",
]

import json
import os
import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from orchestrator.clients.bootstrap import bootstrap_environment
from orchestrator.provenance import resolve_approved_by
from orchestrator.schemas.artifacts import ApplyResult
from orchestrator.schemas.config import TargetConfig
from orchestrator.storage import _wal_write
from orchestrator.storage.lock import acquire_repo_lock, release_repo_lock
from orchestrator.workspace import WorkspaceManager

console = Console()


def _hydrate_apply_result_for_resume(run_dir: Path) -> ApplyResult | None:
    """Hydrate an ApplyResult from the prior run's apply.json for resume.

    Returns None (refuse to resume) if any precondition fails:
    - apply.json missing
    - status is not "applying"
    - pre_apply_diff_backup is missing or points to a non-existent file
    """
    apply_json = run_dir / "apply.json"
    if not apply_json.exists():
        return None

    try:
        data = json.loads(apply_json.read_text(encoding="utf-8"))
        result = ApplyResult(**data)
    except Exception:
        return None

    if result.status != "applying":
        return None

    if not result.pre_apply_diff_backup:
        return None
    if not Path(result.pre_apply_diff_backup).exists():
        return None

    return result


def execute(
    run_id: str,
    allow_dirty: bool = False,
    env_file: Optional[Path] = None,
    workspace: Optional[Path] = None,
    issue_number: Optional[int] = None,
    worker_id: Optional[str] = None,
    coordination_db_dir: Optional[Path] = None,
) -> None:
    """Apply the validated patch to the target repository."""
    console.print(
        Panel(
            f"[bold red]PatchForge Apply Patch (V1)[/bold red]\nRun ID: [yellow]{run_id}[/yellow]",
            expand=False,
        )
    )

    import hashlib
    from datetime import datetime, timezone

    from orchestrator.agents.executor import rollback_to_commit
    from orchestrator.agents.validator import run as run_validator
    from orchestrator.exceptions import RollbackError
    from orchestrator.git import (
        apply_patch,
        create_controlled_branch,
        current_branch,
        current_head,
        repository_state,
        stash_apply,
        stash_create_untracked,
    )
    from orchestrator.lifecycle import classify_lifecycle
    from orchestrator.observability.events import log_event, log_failure
    from orchestrator.schemas.artifacts import (
        ApplyResult,
        PatchLifecycleState,
        compute_auto_apply_eligible,
    )
    from orchestrator.schemas.config import default_workspace_path

    # ===================================================================
    # PROLOGUE — always runs (both happy-path and resume)
    # ===================================================================

    # 1. Resolve workspace path and ensure run exists
    if workspace is not None:
        workspace_path = Path(workspace).resolve()
    elif os.environ.get("PATCHFORGE_WORKSPACE"):
        workspace_path = Path(os.environ["PATCHFORGE_WORKSPACE"]).resolve()
    else:
        workspace_path = default_workspace_path(Path.cwd())

    workspace_mgr = WorkspaceManager(workspace_path)
    try:
        workspace_mgr.ensure_run_exists(run_id)
    except FileNotFoundError as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        raise typer.Exit(code=1) from None

    # 2. Read run.json
    run_metadata = workspace_mgr.read_run_json(run_id)

    if issue_number is None and run_metadata.issue_number is not None:
        issue_number = run_metadata.issue_number

    if run_metadata.status != "previewed":
        _status_msgs = {
            "validation_failed": (
                "Patch validation failed during preview. Review validation.json, "
                "fix the issues, and run preview again."
            ),
            "applied": "This patch has already been applied. Start a new run.",
        }
        msg = _status_msgs.get(
            run_metadata.status,
            f"Run status is '{run_metadata.status}'. "
            "Only successfully previewed runs can be applied.",
        )
        console.print(f"[bold red]Error: {msg}[/bold red]")
        raise typer.Exit(code=1) from None

    target_path = Path(run_metadata.target_path)

    run_metadata.approved_by = resolve_approved_by(target_path)

    # Verify experiment context
    from orchestrator.schemas.experiment import verify_experiment_or_warn

    try:
        verify_experiment_or_warn(workspace_mgr, run_id, target_path)
    except ValueError as exc:
        console.print(f"[bold red]Validation Error: {exc}[/bold red]")
        raise typer.Exit(code=1) from None

    logs_dir = workspace_path / "logs"
    run_dir = workspace_mgr.run_dir(run_id)
    patch_path = run_dir / "patch.diff"

    if not patch_path.exists():
        console.print(f"[bold red]Error: patch.diff does not exist in {run_dir}[/bold red]")
        raise typer.Exit(code=1) from None

    try:
        current_head_sha = current_head(target_path)
    except RuntimeError as exc:
        console.print(f"[bold red]Git Error: {exc}[/bold red]")
        failure_path = run_dir / "failure.json"
        failure_path.write_text(
            json.dumps(
                {
                    "error": "Failed to resolve HEAD",
                    "message": str(exc),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise typer.Exit(code=1) from None

    # Bootstrap target environment & load config (required by validator)
    bootstrap_environment(env_file=env_file, target_path=target_path)
    try:
        config = TargetConfig.load(target_path=target_path, workspace_path=workspace_path)
    except Exception as exc:
        console.print(f"[bold red]Error loading target config: {exc}[/bold red]")
        raise typer.Exit(code=1) from None

    # Verify valid git repo
    try:
        git_state = repository_state(target_path)
    except ValueError as exc:
        console.print(f"[bold red]Git Error: {exc}[/bold red]")
        raise typer.Exit(code=1) from None

    # Classify lifecycle state
    lifecycle_state = classify_lifecycle(run_id, workspace_mgr)

    run_metadata.lifecycle_state = lifecycle_state
    run_metadata.auto_apply_eligible = compute_auto_apply_eligible(
        run_metadata.risk_budget, lifecycle_state, run_metadata.executor_had_errors
    )
    run_metadata.updated_at = datetime.now(timezone.utc)
    workspace_mgr.write_run_json(run_id, run_metadata)

    # ===================================================================
    # BIFURCATION BY LIFECYCLE STATE
    # ===================================================================

    if lifecycle_state is PatchLifecycleState.ALREADY_APPLIED:
        _execute_resume(
            run_id=run_id,
            run_metadata=run_metadata,
            target_path=target_path,
            workspace_mgr=workspace_mgr,
            config=config,
            logs_dir=logs_dir,
            run_dir=run_dir,
            patch_path=patch_path,
            worker_id=worker_id,
            coordination_db_dir=coordination_db_dir,
            run_validator=run_validator,
            rollback_to_commit=rollback_to_commit,
            rollback_error_cls=RollbackError,
            log_event=log_event,
            log_failure=log_failure,
            current_head=current_head,
            current_branch=current_branch,
            stash_apply=stash_apply,
        )
        return

    if lifecycle_state is PatchLifecycleState.CONFLICT:
        console.print(
            f"[bold red]Error: Patch lifecycle state is CONFLICT. "
            f"HEAD {current_head(target_path)} has diverged from base commit "
            f"{run_metadata.base_commit} and the patch cannot be applied cleanly. "
            "Please rebase the patch or generate a new one.[/bold red]"
        )
        raise typer.Exit(code=1) from None

    if lifecycle_state is PatchLifecycleState.STALE:
        console.print(
            "[bold red]Error: Patch lifecycle state is STALE. "
            "The patch.diff is missing, empty, or git could not process it "
            "(git executable not found or invalid patch format). "
            "Please run the preview command again.[/bold red]"
        )
        raise typer.Exit(code=1) from None

    if lifecycle_state is PatchLifecycleState.REBASEABLE:
        console.print(
            "[bold red]Error: Patch lifecycle state is REBASEABLE. "
            f"HEAD {current_head(target_path)} has diverged from base commit "
            f"{run_metadata.base_commit}. The patch still applies cleanly, but "
            "rebasing is blocked in V1. Please generate a new patch for the "
            "current HEAD.[/bold red]"
        )
        raise typer.Exit(code=1) from None

    # ===================================================================
    # HAPPY-PATH (lifecycle_state == VALID)
    # ===================================================================

    # Verify patch checksum (happy-path only; resume trusts prior validation)
    patch_content = patch_path.read_text(encoding="utf-8")
    actual_checksum = hashlib.sha256(patch_content.encode("utf-8")).hexdigest()
    if not run_metadata.patch_checksum:
        console.print("[bold red]Error: Patch checksum is missing. Run preview first.[/bold red]")
        run_metadata.status = "failed"
        run_metadata.apply_status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        if run_metadata.failure_artifacts is None:
            run_metadata.failure_artifacts = []
        if "checksum_mismatch" not in run_metadata.failure_artifacts:
            run_metadata.failure_artifacts.append("checksum_mismatch")
        workspace_mgr.write_run_json(run_id, run_metadata)
        raise typer.Exit(code=1) from None
    if actual_checksum != run_metadata.patch_checksum:
        console.print(
            "[bold red]Error: Patch checksum mismatch. The patch.diff has been modified "
            "since preview.\n"
            f"Expected: {run_metadata.patch_checksum}\n"
            f"Actual:   {actual_checksum}[/bold red]"
        )
        run_metadata.status = "failed"
        run_metadata.apply_status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        if run_metadata.failure_artifacts is None:
            run_metadata.failure_artifacts = []
        if "checksum_mismatch" not in run_metadata.failure_artifacts:
            run_metadata.failure_artifacts.append("checksum_mismatch")
        workspace_mgr.write_run_json(run_id, run_metadata)
        raise typer.Exit(code=1) from None

    # HEAD must match base_commit for the happy-path
    if current_head_sha != run_metadata.base_commit:
        expected = run_metadata.base_commit
        console.print(
            f"[bold red]Error: Repository HEAD has changed since preview. "
            f"Expected {expected}, found {current_head_sha}. "
            "Please re-run scan/preview or rebase/inspect.[/bold red]"
        )
        failure_path = run_dir / "failure.json"
        failure_path.write_text(
            json.dumps(
                {
                    "error": "HEAD has changed",
                    "expected": run_metadata.base_commit,
                    "current": current_head_sha,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise typer.Exit(code=1) from None

    # Check cleanliness (happy-path only)
    if not git_state.is_clean and not allow_dirty:
        console.print(
            "[bold red]Error: Target repository has uncommitted changes. "
            "Please commit, stash them, or run with --allow-dirty.[/bold red]"
        )
        raise typer.Exit(code=1) from None

    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="apply",
        event="stage_start",
        data={
            "lifecycle_state": lifecycle_state,
            "base_commit": run_metadata.base_commit,
            "current_head": current_head(target_path),
        },
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # Save pre-apply Git state
    pre_apply_head = current_head(target_path)
    pre_apply_branch = current_branch(target_path)

    # Snapshot user dirt if --allow-dirty and tree is dirty (§2.5)
    dirty_stash_sha: str | None = None
    dirty_stash_tree: str | None = None
    if allow_dirty and not git_state.is_clean:
        dirty_stash_sha = stash_create_untracked(target_path)
        if dirty_stash_sha:
            from orchestrator.git import rev_parse_tree

            dirty_stash_tree = rev_parse_tree(target_path, dirty_stash_sha)

    # Acquire repo lock before any git mutation
    acquired = False
    repo_identity = str(target_path.resolve())
    if coordination_db_dir is not None:
        acquired = acquire_repo_lock(
            repo_identity,
            worker_id or "unknown",
            ttl_seconds=300,
            db_dir=coordination_db_dir,
        )

    try:
        # Check out explicit, system-controlled Git branch
        if issue_number is not None:
            branch_name = f"patchforge/{run_id}/issue_{issue_number}"
        else:
            branch_name = f"patchforge/{run_id}"

        # Checkpoint 1: status="applying" before any git operation
        apply_result = ApplyResult(
            run_id=run_id,
            applied_at=datetime.now(timezone.utc),
            branch=branch_name,
            success=False,
            pre_apply_head=pre_apply_head,
            pre_apply_branch=pre_apply_branch,
            pre_apply_dirty_stash=dirty_stash_sha,
            pre_apply_dirty_stash_tree=dirty_stash_tree,
            status="applying",
        )
        _wal_write(apply_result, run_dir / "apply.json")

        branch_res = create_controlled_branch(target_path, branch_name)
        if branch_res.return_code != 0:
            console.print(
                f"[bold red]Error checking out branch {branch_name}: {branch_res.stderr}[/bold red]"
            )
            log_failure(
                trace_id=run_id,
                run_id=run_id,
                stage="apply",
                error_type="checkout_failed",
                message=branch_res.stderr,
                logs_dir=logs_dir,
                run_dir=run_dir,
            )
            run_metadata.status = "failed"
            run_metadata.apply_status = "failed"
            run_metadata.updated_at = datetime.now(timezone.utc)
            if run_metadata.failure_artifacts is None:
                run_metadata.failure_artifacts = []
            if "checkout_failure" not in run_metadata.failure_artifacts:
                run_metadata.failure_artifacts.append("checkout_failure")
            workspace_mgr.write_run_json(run_id, run_metadata)
            raise typer.Exit(code=1) from None

        # Apply patch
        backup_path = run_dir / "patch.apply-backup.diff"
        shutil.copy2(patch_path, backup_path)
        apply_result.pre_apply_diff_backup = str(backup_path)
        _wal_write(apply_result, run_dir / "apply.json")

        apply_res = apply_patch(target_path, patch_path)
        if apply_res.return_code != 0:
            console.print(f"[bold red]Error applying patch: {apply_res.stderr}[/bold red]")
            log_failure(
                trace_id=run_id,
                run_id=run_id,
                stage="apply",
                error_type="apply_failed",
                message=apply_res.stderr,
                logs_dir=logs_dir,
                run_dir=run_dir,
            )
            rollback_succeeded = _rollback_with_stash(
                target_path,
                pre_apply_head,
                backup_path,
                dirty_stash_sha,
                rollback_to_commit,
                RollbackError,
                console,
                run_id,
                log_failure,
                logs_dir,
                run_dir,
            )
            apply_result = ApplyResult(
                run_id=run_id,
                applied_at=datetime.now(timezone.utc),
                branch=branch_name,
                success=False,
                status="apply_failed",
                rolled_back=rollback_succeeded,
                error=apply_res.stderr,
                pre_apply_head=pre_apply_head,
                pre_apply_branch=pre_apply_branch,
                pre_apply_dirty_stash=dirty_stash_sha,
                pre_apply_dirty_stash_tree=dirty_stash_tree,
                rollback_head=pre_apply_head if rollback_succeeded else None,
            )
            _wal_write(apply_result, run_dir / "apply.json")
            run_metadata.status = "failed"
            run_metadata.apply_status = "rolled_back" if rollback_succeeded else "rollback_failed"
            run_metadata.updated_at = datetime.now(timezone.utc)
            if run_metadata.failure_artifacts is None:
                run_metadata.failure_artifacts = []
            if "apply.json" not in run_metadata.failure_artifacts:
                run_metadata.failure_artifacts.append("apply.json")
            workspace_mgr.write_run_json(run_id, run_metadata)
            raise typer.Exit(code=1) from None

        # Run post-apply validation checks
        post_val_output = _run_post_apply_validation(
            run_id,
            config,
            run_validator,
            log_event,
            logs_dir,
            run_dir,
        )

        if post_val_output is not None:
            workspace_mgr.write_artifact(
                run_id, "post_apply_validation.json", post_val_output.model_dump_json(indent=2)
            )

        # Handle post-apply validation failure: rollback
        if post_val_output is not None and not post_val_output.overall_passed:
            console.print(
                "[bold yellow]Post-apply validation failed. Rolling back...[/bold yellow]"
            )
            rollback_succeeded = _rollback_with_stash(
                target_path,
                pre_apply_head,
                backup_path,
                dirty_stash_sha,
                rollback_to_commit,
                RollbackError,
                console,
                run_id,
                log_failure,
                logs_dir,
                run_dir,
            )
            failure_detail = {
                "stage": "post_apply_validation",
                "reason": "validation_failed",
                "validation_output": post_val_output.model_dump(),
                "rollback_succeeded": rollback_succeeded,
            }
            workspace_mgr.write_artifact(
                run_id, "post_apply_failure.json", json.dumps(failure_detail, indent=2)
            )
            error_msg = (
                "Post-apply validation failed; rollback also failed"
                if not rollback_succeeded
                else "Post-apply validation failed"
            )
            apply_result = ApplyResult(
                run_id=run_id,
                applied_at=datetime.now(timezone.utc),
                branch=branch_name,
                success=False,
                status="apply_failed",
                rolled_back=rollback_succeeded,
                error=error_msg,
                pre_apply_head=pre_apply_head,
                pre_apply_branch=pre_apply_branch,
                pre_apply_dirty_stash=dirty_stash_sha,
                pre_apply_dirty_stash_tree=dirty_stash_tree,
                rollback_head=pre_apply_head if rollback_succeeded else None,
            )
            _wal_write(apply_result, run_dir / "apply.json")
            run_metadata.status = "failed"
            run_metadata.apply_status = "rolled_back" if rollback_succeeded else "rollback_failed"
            run_metadata.updated_at = datetime.now(timezone.utc)
            if run_metadata.failure_artifacts is None:
                run_metadata.failure_artifacts = []
            for artifact in ["apply.json", "post_apply_failure.json"]:
                if artifact not in run_metadata.failure_artifacts:
                    run_metadata.failure_artifacts.append(artifact)
            workspace_mgr.write_run_json(run_id, run_metadata)
            raise typer.Exit(code=1) from None

        # Checkpoint: status="applied", success=True
        apply_result.applied_at = datetime.now(timezone.utc)
        apply_result.success = True
        apply_result.status = "applied"
        _wal_write(apply_result, run_dir / "apply.json")

        run_metadata.status = "applied"
        run_metadata.apply_status = "success"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)

        log_event(
            trace_id=run_id,
            run_id=run_id,
            level="info",
            source="pipeline",
            stage="apply",
            event="stage_end",
            data={
                "success": True,
                "post_apply_passed": post_val_output.overall_passed if post_val_output else None,
            },
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
    finally:
        if coordination_db_dir is not None and acquired:
            release_repo_lock(repo_identity, worker_id or "unknown", db_dir=coordination_db_dir)

    _print_success(run_id, branch_name, run_metadata)


def _execute_resume(
    *,
    run_id,
    run_metadata,
    target_path,
    workspace_mgr,
    config,
    logs_dir,
    run_dir,
    patch_path,
    worker_id,
    coordination_db_dir,
    run_validator,
    rollback_to_commit,
    rollback_error_cls,
    log_event,
    log_failure,
    current_head,
    current_branch,
    stash_apply,
):
    """Resume an interrupted apply from the ALREADY_APPLIED state."""
    from datetime import datetime, timezone

    # Hydrate WAL from prior run (Ataque 2 defense)
    apply_result = _hydrate_apply_result_for_resume(run_dir)
    if apply_result is None:
        console.print(
            "[bold red]Error: Cannot resume — apply.json is missing, corrupt, "
            "or not in 'applying' state. The backup diff may also be missing. "
            "Please reset the target repo manually and run preview again.[/bold red]"
        )
        raise typer.Exit(code=1) from None

    # Triple isolation verification (Ataque 3 defense)
    actual_branch = current_branch(target_path)
    if actual_branch != apply_result.branch:
        console.print(
            f"[bold red]Error: Split-brain detected — WAL expects branch "
            f"'{apply_result.branch}', but target is on '{actual_branch}'. "
            "Cannot resume safely. Please reset the target repo manually "
            "and run preview again.[/bold red]"
        )
        raise typer.Exit(code=1) from None

    actual_head = current_head(target_path)
    if actual_head != apply_result.pre_apply_head:
        console.print(
            f"[bold red]Error: Split-brain detected — WAL expects HEAD "
            f"'{apply_result.pre_apply_head}', but target HEAD is "
            f"'{actual_head}'. Cannot resume safely.[/bold red]"
        )
        raise typer.Exit(code=1) from None

    console.print(
        "[bold cyan]Resuming interrupted apply — patch already in working tree, "
        "skipping to post-apply validation...[/bold cyan]"
    )

    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="apply",
        event="stage_start",
        data={
            "lifecycle_state": "ALREADY_APPLIED",
            "resumed": True,
            "base_commit": run_metadata.base_commit,
            "current_head": actual_head,
        },
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # Acquire repo lock for the resume path too
    acquired = False
    repo_identity = str(target_path.resolve())
    if coordination_db_dir is not None:
        acquired = acquire_repo_lock(
            repo_identity,
            worker_id or "unknown",
            ttl_seconds=300,
            db_dir=coordination_db_dir,
        )

    try:
        backup_path = Path(apply_result.pre_apply_diff_backup)
        dirty_stash_sha = apply_result.pre_apply_dirty_stash

        # Run post-apply validation checks (the step that was interrupted)
        post_val_output = _run_post_apply_validation(
            run_id,
            config,
            run_validator,
            log_event,
            logs_dir,
            run_dir,
        )

        if post_val_output is not None:
            workspace_mgr.write_artifact(
                run_id, "post_apply_validation.json", post_val_output.model_dump_json(indent=2)
            )

        # Handle validation failure: rollback
        if post_val_output is not None and not post_val_output.overall_passed:
            console.print(
                "[bold yellow]Post-apply validation failed on resume. Rolling back...[/bold yellow]"
            )
            rollback_succeeded = _rollback_with_stash(
                target_path,
                apply_result.pre_apply_head,
                backup_path,
                dirty_stash_sha,
                rollback_to_commit,
                rollback_error_cls,
                console,
                run_id,
                log_failure,
                logs_dir,
                run_dir,
            )
            failure_detail = {
                "stage": "post_apply_validation",
                "reason": "validation_failed_on_resume",
                "validation_output": post_val_output.model_dump(),
                "rollback_succeeded": rollback_succeeded,
            }
            workspace_mgr.write_artifact(
                run_id, "post_apply_failure.json", json.dumps(failure_detail, indent=2)
            )
            error_msg = (
                "Post-apply validation failed on resume; rollback also failed"
                if not rollback_succeeded
                else "Post-apply validation failed on resume"
            )
            # Update WAL incrementally (preserve prior pointers)
            apply_result.applied_at = datetime.now(timezone.utc)
            apply_result.success = False
            apply_result.status = "apply_failed"
            apply_result.rolled_back = rollback_succeeded
            apply_result.error = error_msg
            apply_result.rollback_head = apply_result.pre_apply_head if rollback_succeeded else None
            _wal_write(apply_result, run_dir / "apply.json")
            run_metadata.status = "failed"
            run_metadata.apply_status = "rolled_back" if rollback_succeeded else "rollback_failed"
            run_metadata.updated_at = datetime.now(timezone.utc)
            if run_metadata.failure_artifacts is None:
                run_metadata.failure_artifacts = []
            for artifact in ["apply.json", "post_apply_failure.json"]:
                if artifact not in run_metadata.failure_artifacts:
                    run_metadata.failure_artifacts.append(artifact)
            workspace_mgr.write_run_json(run_id, run_metadata)
            raise typer.Exit(code=1) from None

        # Success: update WAL incrementally
        apply_result.applied_at = datetime.now(timezone.utc)
        apply_result.success = True
        apply_result.status = "applied"
        _wal_write(apply_result, run_dir / "apply.json")

        run_metadata.status = "applied"
        run_metadata.apply_status = "success"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)

        log_event(
            trace_id=run_id,
            run_id=run_id,
            level="info",
            source="pipeline",
            stage="apply",
            event="stage_end",
            data={
                "success": True,
                "resumed": True,
                "post_apply_passed": (post_val_output.overall_passed if post_val_output else None),
            },
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
    finally:
        if coordination_db_dir is not None and acquired:
            release_repo_lock(repo_identity, worker_id or "unknown", db_dir=coordination_db_dir)

    _print_success(run_id, apply_result.branch, run_metadata)


def _run_post_apply_validation(
    run_id,
    config,
    run_validator,
    log_event,
    logs_dir,
    run_dir,
):
    """Run post-apply validation with progress spinner and observability events."""
    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="apply",
        event="post_apply_validation_start",
        data={},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("[green]Running post-apply validation checks...", total=None)
        try:
            post_val_output, _ = run_validator(config=config)
            progress.update(task, completed=100)
        except Exception as exc:
            progress.update(task, completed=100)
            console.print(
                "[bold yellow]Warning: post-apply validation failed to execute: "
                f"{exc}[/bold yellow]"
            )
            post_val_output = None

    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="apply",
        event="post_apply_validation_end",
        data={
            "passed": post_val_output.overall_passed if post_val_output else None,
        },
        logs_dir=logs_dir,
        run_dir=run_dir,
    )
    return post_val_output


def _rollback_with_stash(
    target_path,
    pre_apply_head,
    backup_path,
    dirty_stash_sha,
    rollback_to_commit,
    rollback_error_cls,
    console_obj,
    run_id,
    log_failure,
    logs_dir,
    run_dir,
) -> bool:
    """Rollback to pre-apply state, restoring user dirt from stash if applicable."""
    from orchestrator.git import stash_apply

    rollback_succeeded = False
    try:
        rollback_to_commit(target_path, pre_apply_head, backup_diff=backup_path)
        rollback_succeeded = True
    except rollback_error_cls as exc:
        console_obj.print(
            "[bold red]FATAL: Rollback failed. Your repository may be in a "
            "partially applied state.\n"
            f"Revert stderr: {exc.stderr}\n"
            "Please run 'git checkout .' and 'git clean -fd' manually "
            "to restore a clean state.[/bold red]"
        )
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="apply",
            error_type="rollback_failed",
            message=exc.stderr,
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        return False

    if dirty_stash_sha and rollback_succeeded:
        stash_res = stash_apply(target_path, dirty_stash_sha)
        if stash_res.return_code != 0:
            console_obj.print(
                "[bold yellow]Warning: rollback succeeded but initial user dirt "
                "could not be reapplied. Stash SHA preserved as dangling commit — "
                f"recover with 'git stash apply {dirty_stash_sha}'[/bold yellow]"
            )

    return rollback_succeeded


def _print_success(run_id: str, branch_name: str, run_metadata) -> None:
    eligibility_line = (
        "[green]✔ Auto-apply eligible[/green]"
        if run_metadata.auto_apply_eligible
        else "[yellow]⚠ Manual review recommended[/yellow]"
    )
    console.print(
        Panel(
            "[bold green]Patch applied successfully to branch "
            f"[yellow]{branch_name}[/yellow]![/bold green]\n\n"
            f"{eligibility_line}\n\n"
            "To review and commit the changes, run:\n"
            "  [cyan]git status[/cyan]\n"
            "  [cyan]git diff[/cyan]\n"
            f'  [cyan]git commit -am "Apply patch {run_id}"[/cyan]',
            expand=False,
        )
    )
