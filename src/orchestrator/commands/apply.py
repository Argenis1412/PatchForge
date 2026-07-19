"""Apply the validated patch to the target repository."""

from __future__ import annotations

__all__ = [
    "execute",
]

import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from orchestrator.clients.bootstrap import bootstrap_environment
from orchestrator.provenance import resolve_approved_by
from orchestrator.schemas.config import TargetConfig
from orchestrator.storage import _wal_write
from orchestrator.storage.lock import acquire_repo_lock, release_repo_lock
from orchestrator.workspace import WorkspaceManager

if TYPE_CHECKING:
    from orchestrator.schemas.artifacts import ApplyResult

console = Console()


def _hydrate_apply_result_for_resume(
    run_dir: Path, run_id: str, patch_path: Path
) -> Optional["ApplyResult"]:
    """Load and validate apply.json for a resumable ALREADY_APPLIED state.

    Returns the parsed ApplyResult only if: status == "applying"; the WAL's
    own run_id matches the run being resumed (rejects a WAL copied in from
    another run's directory); the backup diff pointer is the canonical
    run-local path (rejects a pointer redirected elsewhere on disk) and
    refers to an existing regular file; and that file's bytes are identical
    to the current patch.diff (rejects a backup that was swapped or
    corrupted independently of the WAL). Returns None for any other
    condition (missing/corrupt WAL, wrong status, missing/stale/mismatched
    backup) -- callers must treat None as "not resumable, abort".
    """
    from orchestrator.schemas.artifacts import APPLY_JSON, ApplyResult

    apply_json_path = run_dir / APPLY_JSON
    if not apply_json_path.exists():
        return None

    try:
        wal_result = ApplyResult.model_validate_json(apply_json_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if wal_result.run_id != run_id:
        return None

    if wal_result.status != "applying":
        return None

    if not wal_result.pre_apply_diff_backup:
        return None

    backup_path = Path(wal_result.pre_apply_diff_backup)
    canonical_backup_path = run_dir / "patch.apply-backup.diff"
    if backup_path.resolve() != canonical_backup_path.resolve():
        return None
    if not backup_path.is_file():
        return None

    try:
        if backup_path.read_bytes() != patch_path.read_bytes():
            return None
    except OSError:
        return None

    return wal_result


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
        check_orphaned_dirt_stash,
        create_controlled_branch,
        current_branch,
        current_head,
        force_reset_apply,
        repository_state,
        stash_apply_dirt,
        stash_create_dirt,
        stash_drop,
        stash_store_ref,
    )
    from orchestrator.lifecycle import classify_lifecycle
    from orchestrator.observability.events import log_event, log_failure
    from orchestrator.schemas.artifacts import (
        TARGET_CONFIG_SNAPSHOT_JSON,
        ApplyResult,
        PatchLifecycleState,
        compute_auto_apply_eligible,
    )
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

    # Computed early: the resume path needs this to verify the WAL's
    # recorded branch matches what this invocation would create, before any
    # git mutation or lock acquisition happens.
    if issue_number is not None:
        branch_name = f"patchforge/{run_id}/issue_{issue_number}"
    else:
        branch_name = f"patchforge/{run_id}"

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

    # This is the actual human gate — record who is approving now, not at
    # scan/ci time when no approval has happened yet.
    run_metadata.approved_by = resolve_approved_by(target_path)

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

    # Advisory only: warn if a prior --allow-dirty run captured dirt that
    # was never restored (e.g. the process crashed before rollback could
    # run). Cross-reference against known run.json files to avoid a false
    # positive from a stash a user happened to name with this prefix
    # themselves.
    orphan_sha = check_orphaned_dirt_stash(target_path)
    if orphan_sha is not None:
        known_shas = set()
        if workspace_mgr.runs.exists():
            for run_json_path in workspace_mgr.runs.glob("*/run.json"):
                with contextlib.suppress(Exception):
                    data = json.loads(run_json_path.read_text(encoding="utf-8"))
                    sha = data.get("dirt_stash_sha")
                    if sha:
                        known_shas.add(sha)
        if not known_shas or orphan_sha in known_shas:
            console.print(
                "[bold yellow]Warning: found an unresumed --allow-dirty dirt "
                f"capture ({orphan_sha}). Your working-tree state may be "
                f"recoverable via: git stash apply --index {orphan_sha}[/bold yellow]"
            )

    # Acquire repo lock BEFORE any isolation/lifecycle/branch/HEAD checks --
    # including the very first HEAD read immediately below. A
    # lock-acquisition failure means contention with another worker and
    # must abort immediately.
    acquired = False
    repo_identity = str(target_path.resolve())
    if coordination_db_dir is not None:
        acquired = acquire_repo_lock(
            repo_identity,
            worker_id or "unknown",
            ttl_seconds=300,
            db_dir=coordination_db_dir,
        )
        if not acquired:
            console.print(
                "[bold red]Error: Could not acquire the repository lock — another "
                "worker is currently operating on this repository. Aborting.[/bold red]"
            )
            raise typer.Exit(code=1) from None

    try:
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

        # Bootstrap target environment. NOTE: TargetConfig.load() is NOT
        # called here -- it reads orchestrator.json and walks the filesystem,
        # both of which would observe the patch's own (uncommitted) mutations
        # on the ALREADY_APPLIED resume path. It is loaded further down, inside
        # the VALID-only happy path; the resume path loads the pre-apply
        # snapshot instead (see below).
        bootstrap_environment(env_file=env_file, target_path=target_path)

        # Classify lifecycle state using the dedicated lifecycle module.
        lifecycle_state = classify_lifecycle(run_id, workspace_mgr)

        run_metadata.lifecycle_state = lifecycle_state
        run_metadata.auto_apply_eligible = compute_auto_apply_eligible(
            run_metadata.risk_budget, lifecycle_state, run_metadata.executor_had_errors
        )
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

        # 5. Verify patch checksum (runs for VALID and ALREADY_APPLIED alike --
        # the resume path must never trust the WAL without re-checking that
        # patch.diff still matches what was recorded at preview time).
        patch_content = patch_path.read_text(encoding="utf-8")
        actual_checksum = hashlib.sha256(patch_content.encode("utf-8")).hexdigest()
        if not run_metadata.patch_checksum:
            console.print(
                "[bold red]Error: Patch checksum is missing. Run preview first.[/bold red]"
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

        # 6. Save pre-apply Git state (overwritten by WAL values on resume).
        pre_apply_head = current_head(target_path)
        pre_apply_branch = current_branch(target_path)

        # Set on the happy path only when --allow-dirty captures dirt.
        # Always None on the resume path -- the guard above already aborts
        # resume whenever the WAL recorded a dirt capture.
        dirt_stash_sha: Optional[str] = None

        if lifecycle_state is PatchLifecycleState.ALREADY_APPLIED:
            # --- RESUME PATH -----------------------------------------------
            # A previous apply ran git apply successfully but crashed before
            # validation completed. Resume: re-verify isolation against the
            # WAL, reload the pre-apply config snapshot, and fall through to
            # the shared validation/outcome section below -- no branch
            # creation, no git apply (the patch is already in the tree).
            console.print(
                "[bold yellow]Patch lifecycle state is ALREADY_APPLIED. "
                "Attempting automatic resume...[/bold yellow]"
            )

            # Part 3 / Part 4 contract: automatic resume does not (yet)
            # know how to restore dirt captured by a prior --allow-dirty
            # run. Resuming without restoring it would silently lose the
            # user's pre-existing changes, so abort instead of proceeding.
            if run_metadata.dirt_stash_sha:
                console.print(
                    "[bold red]Error: This run captured working-tree dirt with "
                    "--allow-dirty. Automatic resume with dirt is not supported "
                    "yet.\nTo recover your changes: git stash apply --index "
                    f"{run_metadata.dirt_stash_sha}\n"
                    "Then re-run apply on a clean tree.[/bold red]"
                )
                raise typer.Exit(code=1) from None

            wal_result = _hydrate_apply_result_for_resume(run_dir, run_id, patch_path)
            if wal_result is None:
                console.print(
                    "[bold red]Error: ALREADY_APPLIED detected but apply.json is "
                    "missing, corrupt, not in an 'applying' state, or its backup "
                    "diff pointer is missing/stale. Cannot safely resume.\n\n"
                    "To proceed manually:\n"
                    "  - Review the working tree (git status / git diff), then\n"
                    "  - Commit the changes yourself, or\n"
                    "  - Discard them ('git reset --hard "
                    f"{run_metadata.base_commit}' and 'git clean -fd') and "
                    "re-run apply.[/bold red]"
                )
                raise typer.Exit(code=1) from None

            # Triple isolation verification: HEAD, live branch, and
            # residue-free tree (already verified by classify_lifecycle)
            # must all agree with what the WAL recorded. Compare against
            # the LIVE current branch, not the locally-derived branch_name
            # constant -- both branch_name and wal_result.branch are
            # computed by the same deterministic formula from
            # run_id/issue_number, so comparing them to each other can
            # never fail. The real risk is the user switching branches
            # between the crashed run and this resume attempt.
            if wal_result.pre_apply_head != current_head_sha:
                console.print(
                    "[bold red]Resume aborted: recorded pre-apply HEAD "
                    f"'{wal_result.pre_apply_head}' does not match current HEAD "
                    f"'{current_head_sha}'.[/bold red]"
                )
                raise typer.Exit(code=1) from None

            live_branch = current_branch(target_path)
            if live_branch != wal_result.branch:
                console.print(
                    f"[bold red]Resume aborted: current branch '{live_branch}' "
                    f"does not match the branch recorded by the original apply "
                    f"('{wal_result.branch}'). Switch back to that branch before "
                    "retrying, or discard the uncommitted patch.[/bold red]"
                )
                raise typer.Exit(code=1) from None

            config_snapshot_path = run_dir / TARGET_CONFIG_SNAPSHOT_JSON
            if not config_snapshot_path.exists():
                console.print(
                    "[bold red]Resume aborted: target_config_snapshot.json is "
                    "missing. Cannot safely determine the validation "
                    "configuration used at apply time.[/bold red]"
                )
                raise typer.Exit(code=1) from None
            try:
                config = TargetConfig.model_validate_json(
                    config_snapshot_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                console.print(
                    f"[bold red]Resume aborted: failed to load config snapshot: {exc}[/bold red]"
                )
                raise typer.Exit(code=1) from None

            branch_name = wal_result.branch
            pre_apply_head = wal_result.pre_apply_head
            pre_apply_branch = wal_result.pre_apply_branch
            backup_path = Path(wal_result.pre_apply_diff_backup)
            apply_result = wal_result

            log_event(
                trace_id=run_id,
                run_id=run_id,
                level="info",
                source="pipeline",
                stage="apply",
                event="resume_start",
                data={
                    "lifecycle_state": "ALREADY_APPLIED",
                    "wal_branch": wal_result.branch,
                },
                logs_dir=logs_dir,
                run_dir=run_dir,
            )
        else:
            # --- HAPPY PATH (VALID) -----------------------------------------
            # Verify valid git repo and cleanliness here, and only here --
            # ALREADY_APPLIED is expected to have a dirty tree, since the
            # uncommitted patch IS the dirt, so this must not run for that
            # branch at all.
            try:
                git_state = repository_state(target_path)
            except ValueError as exc:
                console.print(f"[bold red]Git Error: {exc}[/bold red]")
                raise typer.Exit(code=1) from None

            if not git_state.is_clean:
                if not allow_dirty:
                    console.print(
                        "[bold red]Error: Target repository has uncommitted changes. "
                        "Please commit, stash them, or run with --allow-dirty.[/bold red]"
                    )
                    raise typer.Exit(code=1) from None
                try:
                    dirt_stash_sha = stash_create_dirt(target_path)
                except ValueError as exc:
                    console.print(f"[bold red]Cannot capture working tree state: {exc}[/bold red]")
                    raise typer.Exit(code=1) from None
                if dirt_stash_sha is not None:
                    if not stash_store_ref(target_path, dirt_stash_sha, f"patchforge:{run_id}"):
                        console.print(
                            "[bold red]Cannot capture working tree state: failed to record "
                            "the dirt capture as a stash entry. Aborting before any mutation "
                            "to avoid leaving your changes unreferenced.[/bold red]"
                        )
                        raise typer.Exit(code=1) from None
                    run_metadata.dirt_stash_sha = dirt_stash_sha
                    workspace_mgr.write_run_json(run_id, run_metadata)
                    reset_res = force_reset_apply(target_path, git_state.head)
                    if reset_res.return_code != 0:
                        console.print(
                            "[bold red]Cannot capture working tree state: failed to reset "
                            f"to a clean tree after capturing dirt: {reset_res.stderr}\n"
                            f"Your changes are safe in: git stash apply --index "
                            f"{dirt_stash_sha}[/bold red]"
                        )
                        raise typer.Exit(code=1) from None

            try:
                config = TargetConfig.load(target_path=target_path, workspace_path=workspace_path)
            except Exception as exc:
                console.print(f"[bold red]Error loading target config: {exc}[/bold red]")
                raise typer.Exit(code=1) from None

            # Checkpoint 1: status="applying" before any git operation.
            apply_result = ApplyResult(
                run_id=run_id,
                applied_at=datetime.now(timezone.utc),
                branch=branch_name,
                success=False,
                pre_apply_head=pre_apply_head,
                pre_apply_branch=pre_apply_branch,
                status="applying",
                dirt_stash_sha=dirt_stash_sha,
            )
            # Persist the pre-apply config snapshot BEFORE the WAL
            # checkpoint (not after): if the process crashes between the
            # two writes, writing the snapshot first means a crash leaves
            # either both files present (resumable) or neither triggering
            # a resume attempt (WAL missing/stale -> abort). Writing the
            # WAL first would instead risk a hydratable WAL with no
            # snapshot -- a resumable-looking state the resume path could
            # never actually complete.
            config_snapshot_path = run_dir / TARGET_CONFIG_SNAPSHOT_JSON
            config_snapshot_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
            _wal_write(apply_result, run_dir / "apply.json")

            branch_res = create_controlled_branch(target_path, branch_name)
            if branch_res.return_code != 0:
                console.print(
                    f"[bold red]Error checking out branch {branch_name}: "
                    f"{branch_res.stderr}[/bold red]"
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
                # No code mutation happened yet (branch checkout itself
                # failed), so there is nothing to code-rollback -- just
                # restore the captured dirt directly onto the still-clean
                # tree left by force_reset_apply above.
                if dirt_stash_sha:
                    if stash_apply_dirt(target_path, dirt_stash_sha):
                        stash_drop(target_path, index=0)
                    else:
                        console.print(
                            "[bold red]FATAL: Branch checkout failed AND restoring your "
                            "pre-existing working-tree changes also failed. Recover them "
                            f"with:\n  git stash apply --index {dirt_stash_sha}[/bold red]"
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
                # Restore captured dirt now that the code rollback succeeded
                # -- restoring before the code rollback would apply the
                # stash onto the wrong tree state. If the code rollback
                # itself failed, the tree is in an unknown state and must
                # not be touched -- but the user still needs to know their
                # dirt is safely captured and how to recover it once the
                # tree is manually fixed.
                dirt_restored = False
                dirt_restore_failed = False
                dirt_recovery_command = None
                if dirt_stash_sha:
                    if rollback_succeeded:
                        if stash_apply_dirt(target_path, dirt_stash_sha):
                            stash_drop(target_path, index=0)
                            dirt_restored = True
                        else:
                            rollback_succeeded = False
                            dirt_restore_failed = True
                            dirt_recovery_command = f"git stash apply --index {dirt_stash_sha}"
                            console.print(
                                "[bold red]FATAL: Code rollback succeeded but restoring your "
                                "pre-existing working-tree changes failed. Recover them with:\n"
                                f"  {dirt_recovery_command}[/bold red]"
                            )
                    else:
                        dirt_restore_failed = True
                        dirt_recovery_command = f"git stash apply --index {dirt_stash_sha}"
                        console.print(
                            "[bold red]Your pre-existing working-tree changes are still "
                            "safely captured. Once you've manually restored a clean state, "
                            f"recover them with:\n  {dirt_recovery_command}[/bold red]"
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
                    dirt_stash_sha=dirt_stash_sha,
                    dirt_restored=dirt_restored,
                    dirt_restore_failed=dirt_restore_failed,
                    dirt_recovery_command=dirt_recovery_command,
                )
                _wal_write(apply_result, run_dir / "apply.json")
                run_metadata.status = "failed"
                run_metadata.apply_status = (
                    "rolled_back" if rollback_succeeded else "rollback_failed"
                )
                run_metadata.updated_at = datetime.now(timezone.utc)
                if run_metadata.failure_artifacts is None:
                    run_metadata.failure_artifacts = []
                if "apply.json" not in run_metadata.failure_artifacts:
                    run_metadata.failure_artifacts.append("apply.json")
                workspace_mgr.write_run_json(run_id, run_metadata)
                raise typer.Exit(code=1) from None

        # --- SHARED: post-apply validation + outcome handling (both the
        # resume path and the happy path fall through to here) -----------

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
                    f"[bold red]Error: post-apply validation failed to execute: {exc}[/bold red]"
                )
                post_val_output = None

        if post_val_output is not None:
            workspace_mgr.write_artifact(
                run_id, "post_apply_validation.json", post_val_output.model_dump_json(indent=2)
            )

        # 10. Handle post-apply validation failure or execution error: roll
        # back automatically. post_val_output is None only when run_validator
        # raised, which must never be treated as an implicit pass.
        if post_val_output is None or not post_val_output.overall_passed:
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
            validation_reason = (
                "validator_errored" if post_val_output is None else "validation_failed"
            )
            failure_detail = {
                "stage": "post_apply_validation",
                "reason": validation_reason,
                "validation_output": (
                    post_val_output.model_dump() if post_val_output is not None else None
                ),
                "rollback_succeeded": rollback_succeeded,
            }
            workspace_mgr.write_artifact(
                run_id, "post_apply_failure.json", json.dumps(failure_detail, indent=2)
            )

            # Restore captured dirt now that the code rollback succeeded. If
            # the code rollback itself failed, the tree is in an unknown
            # state and must not be touched -- but the user still needs the
            # recovery pointer for their safely-captured dirt.
            dirt_restored = False
            dirt_restore_failed = False
            dirt_recovery_command = None
            if dirt_stash_sha:
                if rollback_succeeded:
                    if stash_apply_dirt(target_path, dirt_stash_sha):
                        stash_drop(target_path, index=0)
                        dirt_restored = True
                    else:
                        rollback_succeeded = False
                        dirt_restore_failed = True
                        dirt_recovery_command = f"git stash apply --index {dirt_stash_sha}"
                        console.print(
                            "[bold red]FATAL: Code rollback succeeded but restoring your "
                            "pre-existing working-tree changes failed. Recover them with:\n"
                            f"  {dirt_recovery_command}[/bold red]"
                        )
                else:
                    dirt_restore_failed = True
                    dirt_recovery_command = f"git stash apply --index {dirt_stash_sha}"
                    console.print(
                        "[bold red]Your pre-existing working-tree changes are still "
                        "safely captured. Once you've manually restored a clean state, "
                        f"recover them with:\n  {dirt_recovery_command}[/bold red]"
                    )

            base_error_msg = (
                "Post-apply validation failed to execute"
                if post_val_output is None
                else "Post-apply validation failed"
            )
            error_msg = (
                f"{base_error_msg}; rollback also failed"
                if not rollback_succeeded
                else base_error_msg
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
                dirt_stash_sha=dirt_stash_sha,
                dirt_restored=dirt_restored,
                dirt_restore_failed=dirt_restore_failed,
                dirt_recovery_command=dirt_recovery_command,
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

        # 10.5. On success, restore any captured dirt on top of the applied
        # patch -- --allow-dirty must not silently discard the user's
        # pre-existing changes just because the patch itself succeeded.
        # This can legitimately conflict with the patch's own changes; if
        # so, the patch stays applied (it already passed validation) and
        # the user is pointed at the stash to resolve manually.
        if dirt_stash_sha:
            if stash_apply_dirt(target_path, dirt_stash_sha):
                stash_drop(target_path, index=0)
                apply_result.dirt_restored = True
            else:
                apply_result.dirt_restore_failed = True
                apply_result.dirt_recovery_command = f"git stash apply --index {dirt_stash_sha}"
                console.print(
                    "[bold yellow]Warning: the patch applied and validated successfully, "
                    "but restoring your pre-existing working-tree changes on top of it "
                    "failed (likely a conflict with the patch itself). Recover them with:\n"
                    f"  {apply_result.dirt_recovery_command}[/bold yellow]"
                )

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
