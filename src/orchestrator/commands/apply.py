"""Promote a validated, isolated candidate branch for a previewed run."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn, Optional

import typer
from rich.console import Console
from rich.panel import Panel

from orchestrator.clients.bootstrap import bootstrap_environment
from orchestrator.provenance import resolve_approved_by
from orchestrator.schemas.artifacts import APPLY_JSON
from orchestrator.schemas.config import TargetConfig, default_workspace_path
from orchestrator.storage import _wal_write
from orchestrator.storage.lock import acquire_repo_lock, release_repo_lock
from orchestrator.workspace import WorkspaceManager

console = Console()

APPLY_PROTOCOL = "candidate_promotion@1"
PROMOTION_PREPARED = "promotion_prepared"
PROMOTION_APPLIED = "promotion_applied"


def _candidate_branch(run_id: str, issue_number: int | None) -> str:
    suffix = f"/issue_{issue_number}" if issue_number is not None else ""
    return f"patchforge/{run_id}{suffix}"


def _fail(message: str) -> NoReturn:
    console.print(f"[bold red]Error: {message}[/bold red]")
    raise typer.Exit(code=1)


def _finish(workspace: WorkspaceManager, run_id: str, metadata, result) -> None:
    result.success = True
    result.status = "applied"
    result.promotion_state = PROMOTION_APPLIED
    result.applied_at = datetime.now(timezone.utc)
    _wal_write(result, workspace.run_dir(run_id) / APPLY_JSON)
    metadata.status = "applied"
    metadata.apply_status = "success"
    metadata.updated_at = datetime.now(timezone.utc)
    workspace.write_run_json(run_id, metadata)
    console.print(
        "[bold green]Candidate promoted successfully.[/bold green]\n"
        f"Review it with:\n  git switch {result.branch}"
    )


def execute(
    run_id: str,
    allow_dirty: bool = False,
    env_file: Optional[Path] = None,
    workspace: Optional[Path] = None,
    issue_number: Optional[int] = None,
    worker_id: Optional[str] = None,
    coordination_db_dir: Optional[Path] = None,
    timeout_overrides: dict[str, int] | None = None,
) -> None:
    """Build, validate, and atomically promote an isolated candidate commit.

    ``allow_dirty`` is retained as a CLI compatibility option.  Candidate
    construction never reads or mutates the caller's working tree.
    """
    del allow_dirty
    console.print(
        Panel(
            f"[bold red]PatchForge Candidate Promotion[/bold red]\n"
            f"Run ID: [yellow]{run_id}[/yellow]"
        )
    )
    bootstrap_environment(env_file)

    from orchestrator.agents.validator import run as run_validator
    from orchestrator.git import (
        candidate_worktree,
        commit_candidate,
        current_branch,
        current_head,
        git_common_dir,
        promote_candidate,
        promotion_receipt_ref,
        resolve_ref,
        worktree_is_clean,
    )
    from orchestrator.schemas.artifacts import (
        VALIDATION_JSON,
        VALIDATION_POLICY_JSON,
        ApplyResult,
    )
    from orchestrator.schemas.validator_output import ValidationPolicy, ValidatorOutput
    from orchestrator.validation_decision import (
        attach_validation_decision,
        evaluate_validation,
        expected_validation_subject,
        validation_policy_for,
    )

    workspace_path = (
        Path(workspace).resolve()
        if workspace is not None
        else Path(
            os.environ.get("PATCHFORGE_WORKSPACE", default_workspace_path(Path.cwd()))
        ).resolve()
    )
    manager = WorkspaceManager(workspace_path)
    try:
        manager.ensure_run_exists(run_id)
    except FileNotFoundError as exc:
        _fail(str(exc))
    metadata = manager.read_run_json(run_id)
    if metadata.status not in {"previewed", "applied"}:
        _fail(f"run status is {metadata.status!r}; only previewed runs can be promoted")
    if issue_number is None:
        issue_number = metadata.issue_number
    branch = _candidate_branch(run_id, issue_number)
    candidate_ref = f"refs/heads/{branch}"
    receipt_ref = promotion_receipt_ref(run_id)
    run_dir = manager.run_dir(run_id)
    patch_path = run_dir / "patch.diff"
    if not patch_path.is_file() or not metadata.patch_checksum:
        _fail("preview evidence is incomplete; patch.diff and its checksum are required")
    patch_checksum = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    if patch_checksum != metadata.patch_checksum:
        _fail("patch checksum mismatch; run preview again")

    if metadata.status == "applied":
        console.print("[yellow]Run is already marked applied.[/yellow]")
        return

    lock_dir = coordination_db_dir or workspace_path
    lock_owner = worker_id or run_id
    if not acquire_repo_lock(metadata.target_path, lock_owner, db_dir=lock_dir):
        _fail("another PatchForge operation holds the repository lock")
    try:
        target = Path(metadata.target_path).resolve()
        base_config = TargetConfig.load(
            target_path=target,
            workspace_path=workspace_path,
            timeout_overrides=timeout_overrides,
        )
        git_timeout = base_config.timeouts.git_op
        metadata.approved_by = resolve_approved_by(target)
        manager.write_run_json(run_id, metadata)
        try:
            if git_timeout == 30:
                base_branch = current_branch(target)
                live_base = current_head(target)
            else:
                base_branch = current_branch(target, timeout=git_timeout)
                live_base = current_head(target, timeout=git_timeout)
        except RuntimeError as exc:
            manager.write_artifact(
                run_id,
                "failure.json",
                json.dumps({"error": "Failed to resolve HEAD", "message": str(exc)}, indent=2),
            )
            _fail(f"failed to resolve HEAD: {exc}")
        if base_branch == "HEAD":
            _fail("candidate promotion requires an attached base branch")
        base_ref = f"refs/heads/{base_branch}"
        if live_base != metadata.base_commit:
            _fail("base branch no longer matches this run's base_commit; run preview again")

        wal_path = run_dir / APPLY_JSON
        repository_identity = str(
            git_common_dir(target)
            if git_timeout == 30
            else git_common_dir(target, timeout=git_timeout)
        )

        def recovery_is_authorized(wal: ApplyResult) -> bool:
            if (
                wal.run_id != run_id
                or wal.workspace_path != str(workspace_path)
                or wal.candidate_ref != candidate_ref
                or wal.promotion_receipt_ref != receipt_ref
                or wal.expected_base_ref != base_ref
                or wal.expected_base_commit != metadata.base_commit
                or not wal.candidate_commit
                or not wal.policy_digest
            ):
                return False
            try:
                policy = ValidationPolicy.model_validate_json(
                    (run_dir / VALIDATION_POLICY_JSON).read_text(encoding="utf-8")
                )
                output = ValidatorOutput.model_validate_json(
                    (run_dir / VALIDATION_JSON).read_text(encoding="utf-8")
                )
            except Exception:
                return False
            subject = expected_validation_subject(
                run_id=run_id,
                project_root=target,
                base_commit=metadata.base_commit,
                patch_checksum=patch_checksum,
                candidate_commit=wal.candidate_commit,
                repository_identity=repository_identity,
                policy_digest=wal.policy_digest,
            )
            return (
                policy.digest == wal.policy_digest
                and evaluate_validation(
                    output, fresh=False, expected_subject=subject, expected_policy=policy
                ).authorized
            )

        wal = None
        if wal_path.exists():
            try:
                wal = ApplyResult.model_validate_json(wal_path.read_text(encoding="utf-8"))
            except Exception:
                _fail("apply.json is corrupt; candidate recovery is fail-closed")
            if wal.apply_protocol != APPLY_PROTOCOL:
                _fail("apply.json belongs to a different apply protocol and cannot be resumed")
            if wal.promotion_state == PROMOTION_PREPARED:
                candidate = resolve_ref(target, wal.candidate_ref or "")
                receipt = resolve_ref(target, wal.promotion_receipt_ref or "")
                if candidate == wal.candidate_commit and receipt == wal.candidate_commit:
                    if not recovery_is_authorized(wal):
                        _fail("candidate recovery evidence is incomplete or unauthorized")
                    _finish(manager, run_id, metadata, wal)
                    return
                if candidate is not None or receipt is not None:
                    _fail(
                        "candidate promotion is inconsistent; inspect refs manually before retrying"
                    )
            elif wal.promotion_state == PROMOTION_APPLIED:
                if not recovery_is_authorized(wal):
                    _fail("candidate recovery evidence is incomplete or unauthorized")
                _finish(manager, run_id, metadata, wal)
                return

        candidate_workspace = (
            candidate_worktree(target, metadata.base_commit)
            if git_timeout == 30
            else candidate_worktree(target, metadata.base_commit, timeout=git_timeout)
        )
        with candidate_workspace as candidate_tree:
            # The candidate worktree begins at base_commit, so configuration
            # loaded before the patch is committed is the authorization root.
            policy = validation_policy_for(base_config)
            manager.write_artifact(run_id, VALIDATION_POLICY_JSON, policy.model_dump_json(indent=2))
            message = f"patchforge: apply {run_id}"
            if issue_number is not None:
                message += f" (issue #{issue_number})"
            try:
                candidate_commit = commit_candidate(
                    candidate_tree,
                    patch_path,
                    message,
                    git_timeout=base_config.timeouts.git_op,
                    patch_timeout=base_config.timeouts.patch_apply,
                )
            except RuntimeError as exc:
                _fail(str(exc))
            if not worktree_is_clean(
                candidate_tree, candidate_commit, timeout=base_config.timeouts.git_op
            ):
                _fail("candidate worktree changed while it was being prepared")

            result = ApplyResult(
                run_id=run_id,
                applied_at=datetime.now(timezone.utc),
                branch=branch,
                success=False,
                status="applying",
                apply_protocol=APPLY_PROTOCOL,
                promotion_state=PROMOTION_PREPARED,
                candidate_ref=candidate_ref,
                candidate_commit=candidate_commit,
                promotion_receipt_ref=receipt_ref,
                expected_base_ref=base_ref,
                expected_base_commit=metadata.base_commit,
                policy_digest=policy.digest,
                workspace_path=str(workspace_path),
                pre_apply_head=metadata.base_commit,
                pre_apply_branch=base_branch,
            )
            _wal_write(result, wal_path)

            candidate_config = base_config.model_copy(update={"target_path": candidate_tree})
            output, _ = run_validator(config=candidate_config)
            subject = expected_validation_subject(
                run_id=output.run_id,
                project_root=target,
                base_commit=metadata.base_commit,
                patch_checksum=patch_checksum,
                candidate_commit=candidate_commit,
                repository_identity=repository_identity,
                policy_digest=policy.digest,
            )
            output = attach_validation_decision(
                output, candidate_config, policy=policy, subject=subject
            )
            manager.write_artifact(run_id, "validation.json", output.model_dump_json(indent=2))
            if not worktree_is_clean(
                candidate_tree,
                candidate_commit,
                include_untracked=False,
                timeout=base_config.timeouts.git_op,
            ):
                _fail("validator modified the candidate worktree; refusing promotion")
            decision = evaluate_validation(
                output, fresh=True, expected_subject=subject, expected_policy=policy
            )
            if not decision.authorized:
                _fail("candidate validation was not authorized")
            # The detached worktree keeps the candidate commit reachable until
            # the atomic ref transaction makes the durable promotion refs.
            promoted = promote_candidate(
                target,
                base_ref=base_ref,
                base_commit=metadata.base_commit,
                candidate_ref=candidate_ref,
                candidate_commit=result.candidate_commit or "",
                receipt_ref=receipt_ref,
                timeout=base_config.timeouts.git_op,
            )
            if promoted.return_code != 0:
                _fail(f"candidate promotion failed: {promoted.stderr.strip()}")
        result.promotion_receipt_commit = result.candidate_commit
        _finish(manager, run_id, metadata, result)
    finally:
        release_repo_lock(metadata.target_path, lock_owner, lock_dir)
