"""CI pipeline command: end-to-end scan → plan → preview → apply for headless execution.

Uses low-level agent functions directly (not Typer commands) to avoid
``typer.Exit`` on failure.  Writes a ``CiResult`` JSON file instead of
printing to stdout (agent modules pollute stdout with progress messages).
"""

from __future__ import annotations

__all__ = ["execute"]

import hashlib
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from orchestrator.schemas.ci_result import CiResult

logger = logging.getLogger(__name__)


def _risk_limits(risk_budget: str) -> tuple[int, int]:
    if risk_budget == "medium":
        return 5, 250
    if risk_budget == "high":
        return 10, 500
    return 2, 100


def _write_result(result: CiResult, result_path: Path) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")


def execute(
    target_path: Path,
    workspace_path: Path,
    *,
    issue_file: Optional[Path] = None,
    issue_number: Optional[int] = None,
    risk_budget: str = "low",
    allow_dirty: bool = False,
    result_path: Optional[Path] = None,
    force_provider: Optional[str] = None,
) -> CiResult:
    """Run the full CI pipeline and return a :class:`CiResult`.

    Does NOT call ``git push`` or any GitHub API — those are the caller's
    (workflow runner) responsibility.
    """
    if risk_budget not in ("low", "medium", "high"):
        raise ValueError(
            f"Invalid risk_budget: {risk_budget!r}. Must be 'low', 'medium', or 'high'."
        )
    from orchestrator.agents import architect as architect_agent
    from orchestrator.agents import executor as executor_agent
    from orchestrator.clients.bootstrap import bootstrap_environment
    from orchestrator.git import (
        apply_patch,
        create_controlled_branch,
        current_head,
        repository_identity,
        repository_state,
    )
    from orchestrator.observability.events import log_event, log_failure
    from orchestrator.plan_validation import validate_plan_paths
    from orchestrator.risk import check_patch_gate, check_plan_gate, parse_diff_files
    from orchestrator.scanners.python import scan
    from orchestrator.schemas.artifacts import ApplyResult, RunMetadata, generate_run_id
    from orchestrator.schemas.config import TargetConfig
    from orchestrator.schemas.experiment import Experiment
    from orchestrator.schemas.issue import parse_issue_markdown
    from orchestrator.storage import _wal_write
    from orchestrator.validation_workspace import (
        apply_patch_to_copy,
        create_validation_workspace,
        run_validation_in_copy,
    )
    from orchestrator.workspace import WorkspaceManager

    if result_path is None:
        result_path = workspace_path / "ci_result.json"

    run_id = ""

    def _fail(
        status: str,
        error: str,
        *,
        branch: str = "",
        affected_files: Optional[list[str]] = None,
        validation_passed: bool = False,
    ) -> CiResult:
        r = CiResult(
            run_id=run_id,
            branch=branch,
            status=status,
            risk_budget=risk_budget,
            affected_files=affected_files or [],
            validation_passed=validation_passed,
            error=error,
            issue_number=issue_number,
            force_provider=force_provider,
        )
        _write_result(r, result_path)
        return r

    # ── Bootstrap ──────────────────────────────────────────────────────
    try:
        bootstrap_environment(target_path=target_path)
    except Exception as exc:
        return _fail("scan_failed", f"Bootstrap failed: {exc}")

    try:
        repo_state = repository_state(target_path)
    except ValueError as exc:
        return _fail("scan_failed", str(exc))

    if not allow_dirty and not repo_state.is_clean:
        return _fail(
            "scan_failed",
            "Working tree is not clean. Commit or stash changes, or pass --allow-dirty.",
        )

    workspace_mgr = WorkspaceManager(workspace_path)
    workspace_mgr.setup()

    run_id = generate_run_id()
    run_dir = workspace_mgr.create_run_directory(run_id)
    logs_dir = workspace_path / "logs"

    try:
        config = TargetConfig.load(target_path=target_path, workspace_path=workspace_path)
    except Exception as exc:
        return _fail("scan_failed", f"Config load failed: {exc}")

    log_event(
        trace_id=run_id,
        run_id=run_id,
        source="ci",
        stage="scan",
        event="pipeline_start",
        data={"target": str(target_path), "mode": "ci"},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # ── SCAN ───────────────────────────────────────────────────────────
    try:
        findings = scan(target_path, config.ignore_dirs)
    except Exception as exc:
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="scan",
            error_type="scanner_failed",
            message=str(exc),
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        return _fail("scan_failed", f"Scanner failed: {exc}")

    max_files, max_diff_lines = _risk_limits(risk_budget)
    now = datetime.now(timezone.utc)
    run_metadata = RunMetadata(
        run_id=run_id,
        target_path=str(target_path),
        workspace_path=str(workspace_path),
        base_commit=findings.base_commit,
        branch=findings.branch,
        status="scanned",
        created_at=now,
        updated_at=now,
        v1_supported=findings.v1_supported,
        support_reasons=findings.support_reasons,
        risk_budget=risk_budget,
        max_files=max_files,
        max_diff_lines=max_diff_lines,
        issue_number=issue_number,
    )
    workspace_mgr.write_run_json(run_id, run_metadata)
    workspace_mgr.write_artifact(run_id, "findings.json", findings.model_dump_json(indent=2))

    if not findings.v1_supported:
        return _fail("scan_failed", "V1 not supported: " + "; ".join(findings.unsupported_reasons))

    # ── PLAN ───────────────────────────────────────────────────────────
    log_event(
        trace_id=run_id,
        run_id=run_id,
        source="ci",
        stage="architect",
        event="stage_start",
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    goal: str
    try:
        if issue_file is not None:
            raw = issue_file.read_text(encoding="utf-8")
            issue_input = parse_issue_markdown(raw)
            workspace_mgr.write_artifact(run_id, "issue.md", raw)
            arch_output, arch_meta = architect_agent.run_from_issue(
                issue_input,
                config=config,
                trace_id=run_id,
                run_id=run_id,
                force_provider=force_provider,
            )
            goal = issue_input.title
        else:
            from orchestrator.schemas.scout_output import ScoutOutput

            findings_content = workspace_mgr.read_artifact(run_id, "findings.json")
            scout_output = ScoutOutput.model_validate_json(findings_content)
            arch_output, arch_meta = architect_agent.run(
                scout_output,
                config=config,
                trace_id=run_id,
                run_id=run_id,
                force_provider=force_provider,
            )
            goal = scout_output.summary
    except Exception as exc:
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="architect",
            error_type="architect_failed",
            message=str(exc),
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        return _fail("plan_failed", f"Architect failed: {exc}")

    for plan_task in arch_output.implementation_plan:
        if plan_task.risk_level == "high":
            plan_task.status = "blocked"

    risk_result = check_plan_gate(run_metadata, arch_output, workspace_mgr=workspace_mgr)
    if not risk_result.passed:
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        return _fail("plan_failed", "Risk gate blocked: " + "; ".join(risk_result.reasons))

    path_reasons = validate_plan_paths(arch_output, target_path)
    if path_reasons:
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="architect",
            error_type="plan_references_missing_files",
            message="; ".join(path_reasons),
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        return _fail("plan_failed", "Invalid file references: " + "; ".join(path_reasons))

    workspace_mgr.write_artifact(run_id, "plan.json", arch_output.model_dump_json(indent=2))

    # Experiment context
    try:
        target_sha = current_head(target_path)
        repo_id = repository_identity(target_path)
        experiment = Experiment(
            run_id=run_id,
            plan=arch_output,
            target_commit_sha=target_sha,
            repository_identity=repo_id,
            workspace_path=workspace_path,
        )
        workspace_mgr.write_experiment(run_id, experiment)
    except RuntimeError as exc:
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="architect",
            error_type="experiment_capture_failed",
            message=str(exc),
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        return _fail("plan_failed", f"Experiment capture failed: {exc}")

    files = set()
    for t in arch_output.implementation_plan:
        files.update(t.files_to_modify)
    run_metadata.affected_files = sorted(files)
    run_metadata.goal = goal
    run_metadata.status = "planned"
    run_metadata.updated_at = datetime.now(timezone.utc)
    workspace_mgr.write_run_json(run_id, run_metadata)

    log_event(
        trace_id=run_id,
        run_id=run_id,
        source="ci",
        stage="architect",
        event="stage_end",
        data={"cost_usd": arch_meta.get("cost_usd"), "tasks": len(arch_output.implementation_plan)},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # ── PREVIEW (executor + isolated validation) ───────────────────────
    log_event(
        trace_id=run_id,
        run_id=run_id,
        source="ci",
        stage="executor",
        event="stage_start",
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    if force_provider is not None:
        log_event(
            trace_id=run_id,
            run_id=run_id,
            source="ci",
            stage="executor",
            event="force_provider_override",
            data={"provider": force_provider, "source": "cli"},
            logs_dir=logs_dir,
            run_dir=run_dir,
        )

    staging_dir = run_dir / "staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        executor_output, exec_meta = executor_agent.run(
            architect_output=arch_output,
            run_id=run_id,
            config=config,
            staging_dir=staging_dir,
            force_provider=force_provider,
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
    except Exception as exc:
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="executor",
            error_type="executor_failed",
            message=str(exc),
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        return _fail("preview_failed", f"Executor failed: {exc}")

    diffs = []
    for change in executor_output.applied + executor_output.pending_review:
        if change.diff:
            diffs.append(change.diff)
    patch_diff = "\n".join(diffs)

    if not patch_diff:
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        return _fail("preview_failed", "No diffs generated (empty patch)")

    patch_risk = check_patch_gate(run_metadata, patch_diff, workspace_mgr=workspace_mgr)
    if not patch_risk.passed:
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        return _fail("preview_failed", "Patch gate blocked: " + "; ".join(patch_risk.reasons))

    workspace_mgr.write_artifact(run_id, "patch.diff", patch_diff)
    patch_checksum = hashlib.sha256(patch_diff.encode("utf-8")).hexdigest()

    log_event(
        trace_id=run_id,
        run_id=run_id,
        source="ci",
        stage="executor",
        event="stage_end",
        data={"cost_usd": exec_meta.get("cost_usd"), "applied": len(executor_output.applied)},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # Validate in isolated copy (FA-1: never on the real tree)
    log_event(
        trace_id=run_id,
        run_id=run_id,
        source="ci",
        stage="validator",
        event="stage_start",
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    patch_path = run_dir / "patch.diff"
    validator_output = None
    try:
        with create_validation_workspace(
            original_root=target_path, patch_path=patch_path
        ) as val_ws:
            apply_res = apply_patch_to_copy(val_ws.temporary_root, val_ws.patch_path)
            if apply_res.return_code != 0:
                from orchestrator.schemas.validator_output import ValidatorOutput

                validator_output = ValidatorOutput(
                    overall_passed=False,
                    tools=[],
                    llm_summary=f"Patch application failed in validation: {apply_res.stderr}",
                    run_id=run_id,
                )
            else:
                validator_output = run_validation_in_copy(val_ws.temporary_root, config)
    except Exception as exc:
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="validator",
            error_type="validator_failed",
            message=str(exc),
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        return _fail("preview_failed", f"Validator failed: {exc}")

    workspace_mgr.write_artifact(
        run_id, "validation.json", validator_output.model_dump_json(indent=2)
    )

    validation_summary = (
        "All checks passed"
        if validator_output.overall_passed
        else (validator_output.llm_summary or "Validation failed")
    )
    run_metadata.patch_checksum = patch_checksum
    run_metadata.validation_summary = validation_summary
    run_metadata.model_metadata = {
        "executor": exec_meta,
        "validator": {
            "model_used": validator_output.model_used_for_summary,
            "overall_passed": validator_output.overall_passed,
        },
    }
    run_metadata.provider_config = exec_meta.get("models_resolved")

    if not validator_output.overall_passed:
        run_metadata.status = "validation_failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        return _fail("preview_failed", f"Validation failed: {validation_summary}")

    run_metadata.status = "previewed"
    run_metadata.updated_at = datetime.now(timezone.utc)
    workspace_mgr.write_run_json(run_id, run_metadata)

    log_event(
        trace_id=run_id,
        run_id=run_id,
        source="ci",
        stage="validator",
        event="stage_end",
        data={"overall_passed": True},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # ── APPLY (to real working tree, NO push) ──────────────────────────
    log_event(
        trace_id=run_id,
        run_id=run_id,
        source="ci",
        stage="apply",
        event="stage_start",
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    branch_name = f"patchforge/{run_id}"
    pre_apply_head = current_head(target_path)

    apply_result = ApplyResult(
        run_id=run_id,
        applied_at=datetime.now(timezone.utc),
        branch=branch_name,
        success=False,
        pre_apply_head=pre_apply_head,
        status="applying",
    )
    _wal_write(apply_result, run_dir / "apply.json")

    affected = run_metadata.affected_files or []

    def _apply_fail(error: str, *, rolled_back: bool = False) -> CiResult:
        apply_result.error = error
        apply_result.rolled_back = rolled_back
        _wal_write(apply_result, run_dir / "apply.json")
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        return _fail(
            "apply_failed",
            error,
            branch=branch_name,
            affected_files=affected,
            validation_passed=True,
        )

    def _rollback() -> bool:
        from orchestrator.agents.executor import rollback_to_commit

        try:
            rollback_to_commit(target_path, pre_apply_head)
            return True
        except Exception:
            return False

    branch_res = create_controlled_branch(target_path, branch_name)
    if branch_res.return_code != 0:
        return _apply_fail(f"Branch creation failed: {branch_res.stderr}")

    apply_res = apply_patch(target_path, patch_path)
    if apply_res.return_code != 0:
        rolled = _rollback()
        return _apply_fail(f"Patch apply failed: {apply_res.stderr}", rolled_back=rolled)

    # Stage only files touched by the patch (not untracked generated files)
    patch_text = patch_path.read_text(encoding="utf-8")
    staged_files = sorted(parse_diff_files(patch_text))
    if not staged_files:
        return _apply_fail("patch has no recognizable file headers — refusing blind staging")

    commit_msg = f"patchforge: apply {run_id}"
    if issue_number is not None:
        commit_msg += f" (issue #{issue_number})"
    ar = subprocess.run(
        ["git", "-C", str(target_path), "add", "--", *staged_files],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if ar.returncode != 0:
        rolled = _rollback()
        return _apply_fail(f"git add failed: {ar.stderr}", rolled_back=rolled)

    cr = subprocess.run(
        ["git", "-C", str(target_path), "commit", "-m", commit_msg],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if cr.returncode != 0:
        rolled = _rollback()
        return _apply_fail(f"git commit failed: {cr.stderr}", rolled_back=rolled)

    apply_result.success = True
    apply_result.status = "committed_local"
    apply_result.applied_at = datetime.now(timezone.utc)
    _wal_write(apply_result, run_dir / "apply.json")

    run_metadata.status = "applied"
    run_metadata.apply_status = "success"
    run_metadata.updated_at = datetime.now(timezone.utc)
    workspace_mgr.write_run_json(run_id, run_metadata)

    log_event(
        trace_id=run_id,
        run_id=run_id,
        source="ci",
        stage="apply",
        event="stage_end",
        data={"success": True, "branch": branch_name},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    result = CiResult(
        run_id=run_id,
        branch=branch_name,
        status="applied",
        risk_budget=risk_budget,
        affected_files=run_metadata.affected_files or [],
        validation_passed=True,
        issue_number=issue_number,
        force_provider=force_provider,
    )
    _write_result(result, result_path)
    return result
