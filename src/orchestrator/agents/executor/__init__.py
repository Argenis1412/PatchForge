from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from orchestrator.exceptions import SchedulerInvariantError
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.executor_output import ExecutorOutput, FileChange, TaskStatus

from . import applier as _applier
from .logging import _get_logger
from .providers import MODEL_CLAUDE, MODEL_GEMINI, MODEL_GROQ
from .rollback import rollback_to_commit as rollback_to_commit
from .scheduler import _build_dag, _topological_order

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(
    os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parent.parent.parent.parent))
)

LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"

# ---------------------------------------------------------------------------
# Public Entrypoint
# ---------------------------------------------------------------------------


def run(
    architect_output: ArchitectOutput,
    config: Optional[Union[str, Path, TargetConfig]] = None,
    staging_dir: Optional[Path] = None,
) -> tuple[ExecutorOutput, dict]:
    if config is None:
        config = TargetConfig.load(target_path=PROJECT_ROOT)
    elif isinstance(config, (str, Path)):
        config = TargetConfig.load(target_path=Path(config))

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    logs_dir = config.workspace_path / "logs"
    project_root = config.target_path.resolve()
    if staging_dir is None:
        staging_dir = config.workspace_path / "outputs" / "staging" / run_id

    # Initialize logger
    _get_logger(logs_dir)
    _get_logger().info("=== Executor run %s ===", run_id)

    model_string = f"GM:{MODEL_GEMINI}|GQ:{MODEL_GROQ}|CL:{MODEL_CLAUDE}"
    result = ExecutorOutput(model=model_string, run_id=run_id)

    total_tokens_input = 0
    total_tokens_output = 0

    tasks = architect_output.implementation_plan
    dag = _build_dag(tasks)
    ordered_tasks = _topological_order(tasks, dag)
    task_status_results: dict[str, TaskStatus] = {}

    for task in ordered_tasks:
        _get_logger().info(
            "[%s] Task %s | risk=%s | title=%s", run_id, task.task_id, task.risk_level, task.title
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
            change = _applier._apply_task(single_file_task, run_id, project_root, staging_dir)

            result.total_tokens += change.tokens_used
            result.total_cost_usd += change.cost_usd

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

    _get_logger().info(
        "[%s] Finished | applied=%d | pending_review=%d | errors=%d | cost=$%.6f",
        run_id,
        len(result.applied),
        len(result.pending_review),
        len(result.errors),
        result.total_cost_usd,
    )

    meta = {
        "tokens_input": total_tokens_input,
        "tokens_output": total_tokens_output,
        "cost_usd": result.total_cost_usd,
        "model_used": model_string,
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
