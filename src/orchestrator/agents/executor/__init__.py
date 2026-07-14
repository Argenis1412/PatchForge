"""Executor agent: executes planned tasks via LLM providers with DAG scheduling."""

from __future__ import annotations

__all__ = [
    "PROJECT_ROOT",
    "rollback_to_commit",
    "run",
]

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from orchestrator import paths as _paths
from orchestrator.exceptions import SchedulerInvariantError
from orchestrator.observability.events import log_event
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.executor_output import ExecutorOutput, FileChange, TaskStatus

from . import applier as _applier
from .logging import _get_logger
from .providers import KNOWN_PROVIDER_NAMES, MODEL_CLAUDE, _get_model, init_provider_models
from .rollback import rollback_to_commit as rollback_to_commit
from .scheduler import _build_dag, _topological_order

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Re-export for compatibility; monkeypatch should target orchestrator.paths
PROJECT_ROOT = _paths.PROJECT_ROOT

# ---------------------------------------------------------------------------
# Public Entrypoint
# ---------------------------------------------------------------------------


def _safe_log_event(
    trace_id: str, run_id: str, event: str, data: dict, logs_dir: Path, run_dir: Optional[Path]
) -> None:
    try:
        log_event(
            trace_id=trace_id,
            run_id=run_id,
            source="executor",
            stage="executor",
            event=event,
            data=data,
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
    except OSError as exc:
        _get_logger().warning("[%s] Failed to emit event %s: %s", run_id, event, exc)


def run(
    architect_output: ArchitectOutput,
    run_id: Optional[str] = None,
    config: Optional[Union[str, Path, TargetConfig]] = None,
    staging_dir: Optional[Path] = None,
    force_provider: Optional[str] = None,
    logs_dir: Optional[Path] = None,
    run_dir: Optional[Path] = None,
    trace_id: Optional[str] = None,
) -> tuple[ExecutorOutput, dict]:
    if config is None:
        config = TargetConfig.load(target_path=_paths.PROJECT_ROOT)
    elif isinstance(config, (str, Path)):
        config = TargetConfig.load(target_path=Path(config))

    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    effective_trace_id = trace_id or run_id
    file_logs_dir = config.workspace_path / "logs"
    project_root = config.target_path.resolve()
    if staging_dir is None:
        staging_dir = config.workspace_path / "outputs" / "staging" / run_id
    event_logs_dir = logs_dir if logs_dir is not None else file_logs_dir

    # Initialize logger
    _get_logger(file_logs_dir)
    _get_logger().info("=== Executor run %s ===", run_id)

    if force_provider is not None:
        if force_provider not in KNOWN_PROVIDER_NAMES:
            raise ValueError(
                f"Unknown provider: {force_provider}. Available: {KNOWN_PROVIDER_NAMES}"
            )
        _get_logger().info("[%s] force_provider override active: %s", run_id, force_provider)

    resolved = init_provider_models(config)
    model_string = (
        f"GM:{_get_model('gemini')}|OR:{_get_model('openrouter')}|CL:{_get_model('claude')}"
    )
    claude_override_used = _get_model("claude") != MODEL_CLAUDE
    providers_used: set[str] = set()
    result = ExecutorOutput(model=model_string, run_id=run_id)

    total_tokens_input = 0
    total_tokens_output = 0

    tasks = architect_output.implementation_plan
    dag = _build_dag(tasks)
    ordered_tasks = _topological_order(tasks, dag)
    task_status_results: dict[str, TaskStatus] = {}

    _safe_log_event(
        effective_trace_id,
        run_id,
        "executor_start",
        {"run_id": run_id, "task_count": len(tasks)},
        event_logs_dir,
        run_dir,
    )

    for task in ordered_tasks:
        _get_logger().info(
            "[%s] Task %s | risk=%s | title=%s", run_id, task.task_id, task.risk_level, task.title
        )
        _safe_log_event(
            effective_trace_id,
            run_id,
            "task_start",
            {"task_id": task.task_id, "title": task.title, "risk_level": task.risk_level},
            event_logs_dir,
            run_dir,
        )

        # --- dependency check ---
        skip = False
        for dep_id in task.dependencies:
            if dep_id not in task_status_results:
                raise SchedulerInvariantError(
                    f"Task {task.task_id} depends on {dep_id}, but {dep_id} was never scheduled"
                )
            dep_status = task_status_results[dep_id]
            if dep_status in {TaskStatus.ERROR, TaskStatus.SKIPPED, TaskStatus.PENDING_REVIEW}:
                _get_logger().info(
                    "[%s] Task %s — SKIPPED (dependency %s has status %s)",
                    run_id,
                    task.task_id,
                    dep_id,
                    dep_status,
                )
                _safe_log_event(
                    effective_trace_id,
                    run_id,
                    "task_skipped",
                    {
                        "task_id": task.task_id,
                        "dependency": dep_id,
                        "dependency_status": str(dep_status),
                    },
                    event_logs_dir,
                    run_dir,
                )
                result.errors.append(
                    FileChange(
                        task_id=task.task_id,
                        file=task.files_to_modify[0] if task.files_to_modify else "",
                        status=TaskStatus.SKIPPED,
                        diff=None,
                        original_content=None,
                        modified_content=None,
                        error=f"dependency {dep_id} has status {dep_status}",
                        tokens_used=0,
                        cost_usd=0.0,
                    )
                )
                task_status_results[task.task_id] = TaskStatus.SKIPPED
                skip = True
                break

        if skip:
            continue

        # --- execute task (all dependencies satisfied) ---
        task_statuses: list[TaskStatus] = []
        for file_relative in task.files_to_modify:
            single_file_task = task.model_copy(update={"files_to_modify": [file_relative]})
            _safe_log_event(
                effective_trace_id,
                run_id,
                "file_start",
                {"task_id": task.task_id, "file": file_relative},
                event_logs_dir,
                run_dir,
            )
            change = _applier._apply_task(
                single_file_task, run_id, project_root, staging_dir, force_provider=force_provider
            )
            _safe_log_event(
                effective_trace_id,
                run_id,
                "file_end",
                {
                    "task_id": task.task_id,
                    "file": file_relative,
                    "status": str(change.status),
                    "tokens_used": change.tokens_used,
                    "cost_usd": change.cost_usd,
                },
                event_logs_dir,
                run_dir,
            )

            result.total_tokens += change.tokens_used
            result.total_cost_usd += change.cost_usd
            if change.provider_name:
                providers_used.add(change.provider_name)

            # Simple heuristic for token tracking: _apply_task returns combined tokens_used
            total_tokens_input += change.tokens_used // 2
            total_tokens_output += change.tokens_used // 2

            # Route per-file change by status
            if change.status in {TaskStatus.APPLIED, TaskStatus.NOOP}:
                result.applied.append(change)
            elif change.status == TaskStatus.PENDING_REVIEW:
                result.pending_review.append(change)
            else:
                result.errors.append(change)

            task_statuses.append(change.status)

        # Aggregate: worst status wins for dependency tracking
        if TaskStatus.ERROR in task_statuses:
            task_status_results[task.task_id] = TaskStatus.ERROR
        elif TaskStatus.PENDING_REVIEW in task_statuses:
            task_status_results[task.task_id] = TaskStatus.PENDING_REVIEW
        elif TaskStatus.APPLIED in task_statuses:
            task_status_results[task.task_id] = TaskStatus.APPLIED
        else:
            task_status_results[task.task_id] = TaskStatus.NOOP

        _safe_log_event(
            effective_trace_id,
            run_id,
            "task_end",
            {
                "task_id": task.task_id,
                "status": str(task_status_results[task.task_id]),
                "file_count": len(task_statuses),
            },
            event_logs_dir,
            run_dir,
        )

    _get_logger().info(
        "[%s] Finished | applied=%d | pending_review=%d | errors=%d | cost=$%.6f",
        run_id,
        len(result.applied),
        len(result.pending_review),
        len(result.errors),
        result.total_cost_usd,
    )

    _safe_log_event(
        effective_trace_id,
        run_id,
        "executor_end",
        {
            "applied": len(result.applied),
            "pending_review": len(result.pending_review),
            "errors": len(result.errors),
            "total_cost_usd": result.total_cost_usd,
        },
        event_logs_dir,
        run_dir,
    )

    cost_llm_null = claude_override_used and "claude" in providers_used
    if cost_llm_null:
        _get_logger().warning(
            "[%s] cost_usd reflects only known-cost providers; "
            "Claude cost unknown due to model override",
            run_id,
        )

    meta = {
        "tokens_input": total_tokens_input,
        "tokens_output": total_tokens_output,
        "cost_usd": result.total_cost_usd,
        "model_used": model_string,
        "models_resolved": resolved,
        "cost_llm": None if cost_llm_null else result.total_cost_usd,
    }

    return result, meta


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m orchestrator.agents.executor <architect_output.json>")
        sys.exit(1)

    architect_json_path = Path(sys.argv[1])
    if not architect_json_path.exists():
        print(f"File not found: {architect_json_path}")
        sys.exit(1)

    architect_data = json.loads(architect_json_path.read_text(encoding="utf-8"))
    architect_output = ArchitectOutput.model_validate(architect_data)

    result, _ = run(architect_output)

    print(f"\n[OK] Applied       : {len(result.applied)}")
    print(f"[~] Pending review : {len(result.pending_review)}")
    print(f"[X] Errors         : {len(result.errors)}")
    print(f"[$] Total cost     : ${result.total_cost_usd:.6f}")

    if result.applied:
        print("\n--- Applied diffs ---")
        for change in result.applied:
            print(f"\n[{change.task_id}] {change.file}")
            print(change.diff)

    if result.pending_review:
        print("\n--- PENDING diffs (HIGH risk, not written) ---")
        for change in result.pending_review:
            print(f"\n[{change.task_id}] {change.file}")
            print(change.diff)
