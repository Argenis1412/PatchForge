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
from orchestrator.schemas.config import TargetConfig
from orchestrator.storage import _wal_write
from orchestrator.storage.lock import acquire_repo_lock, release_repo_lock
from orchestrator.workspace import WorkspaceManager

console = Console()


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
    )
    from orchestrator.lifecycle import classify_lifecycle
    from orchestrator.observability.events import log_event, log_failure
    from orchestrator.schemas.artifacts import ApplyResult, PatchLifecycleState
    from orchestrator.schemas.config import default_workspace_path

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

    # 2. Read run.json and patch.diff
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

    # 2.5 Verify experiment context if experiment.json is present
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

    # 3. Bootstrap target environment & load config
    bootstrap_environment(env_file=env_file, target_path=target_path)
    try:
        config = TargetConfig.load(target_path=target_path, workspace_path=workspace_path)
    except Exception as exc:
        console.print(f"[bold red]Error loading target config: {exc}[/bold red]")
        raise typer.Exit(code=1) from None

    # 4. Perform Git Safety Checks
    # Verify valid git repo
    try:
        git_state = repository_state(target_path)
    except ValueError as exc:
        console.print(f"[bold red]Git Error: {exc}[/bold red]")
        raise typer.Exit(code=1) from None

    # Check cleanliness
    if not git_state.is_clean and not allow_dirty:
        console.print(
            "[bold red]Error: Target repository has uncommitted changes. "
            "Please commit, stash them, or run with --allow-dirty.[/bold red]"
        )
        raise typer.Exit(code=1) from None

    # Classify lifecycle state using the dedicated lifecycle module.
    lifecycle_state = classify_lifecycle(run_id, workspace_mgr)

    run_metadata.lifecycle_state = lifecycle_state
    run_metadata.updated_at = datetime.now(timezone.utc)
    workspace_mgr.write_run_json(run_id, run_metadata)

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

    # 5. Verify patch checksum
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

    # 6. Save pre-apply Git state
    pre_apply_head = current_head(target_path)
    pre_apply_branch = current_branch(target_path)

    # 6b. Acquire repo lock before any git mutation
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
        # 7. Check out explicit, system-controlled Git branch
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

        # 8. Apply patch
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
            # Revert: force reset to pre-apply state
            rollback_succeeded = False
            try:
                rollback_to_commit(target_path, pre_apply_head, backup_diff=backup_path)
                rollback_succeeded = True
            except RollbackError as exc:
                console.print(
                    "[bold red]FATAL: Patch application failed AND the automatic revert also "
                    "failed. Your repository may be in a partially applied state.\n"
                    f"Revert stderr: {exc.stderr}\n"
                    "Please run 'git checkout .' and 'git clean -fd' manually "
                    "to restore a clean state.[/bold red]"
                )
                log_failure(
                    trace_id=run_id,
                    run_id=run_id,
                    stage="apply",
                    error_type="revert_failed",
                    message=exc.stderr,
                    logs_dir=logs_dir,
                    run_dir=run_dir,
                )
            # Write apply.json failure using ApplyResult model
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

        # 9. Run post-apply validation checks
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

        if post_val_output is not None:
            workspace_mgr.write_artifact(
                run_id, "post_apply_validation.json", post_val_output.model_dump_json(indent=2)
            )

        # 10. Handle post-apply validation failure: rollback automatically
        if post_val_output is not None and not post_val_output.overall_passed:
            console.print(
                "[bold yellow]Post-apply validation failed. Rolling back...[/bold yellow]"
            )
            rollback_succeeded = False
            try:
                rollback_to_commit(target_path, pre_apply_head, backup_diff=backup_path)
                rollback_succeeded = True
            except RollbackError as exc:
                console.print(
                    "[bold red]FATAL: Post-apply validation failed AND automatic rollback "
                    "also failed. Your repository may be in a partially applied state.\n"
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

            # Write post_apply_failure.json
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

        # 11. Checkpoint 5: status="applied", success=True
        apply_result.applied_at = datetime.now(timezone.utc)
        apply_result.success = True
        apply_result.status = "applied"
        _wal_write(apply_result, run_dir / "apply.json")

        # 12. Update metadata
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

    console.print(
        Panel(
            "[bold green]Patch applied successfully to branch "
            f"[yellow]{branch_name}[/yellow]![/bold green]\n\n"
            "To review and commit the changes, run:\n"
            "  [cyan]git status[/cyan]\n"
            "  [cyan]git diff[/cyan]\n"
            f'  [cyan]git commit -am "Apply patch {run_id}"[/cyan]',
            expand=False,
        )
    )
