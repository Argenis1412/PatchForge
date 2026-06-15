from __future__ import annotations

from orchestrator.exceptions import CycleDetectedError, SchedulerInvariantError
from orchestrator.schemas.architect_output import Task


def _build_dag(tasks: list[Task]) -> dict[str, set[str]]:
    known_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for task in tasks:
        if task.task_id in known_ids:
            duplicate_ids.add(task.task_id)
        known_ids.add(task.task_id)
    if duplicate_ids:
        dup_list = ", ".join(sorted(duplicate_ids))
        raise SchedulerInvariantError(f"Duplicate task_id(s) in plan: {dup_list}")
    dag: dict[str, set[str]] = {}
    for task in tasks:
        for dep in task.dependencies:
            if dep not in known_ids:
                raise SchedulerInvariantError(
                    f"Task {task.task_id} depends on {dep}, but {dep} does not exist in the plan"
                )
        dag[task.task_id] = set(task.dependencies)
    return dag


def _topological_order(tasks: list[Task], dag: dict[str, set[str]]) -> list[Task]:
    task_map = {t.task_id: t for t in tasks}
    remaining = set(dag.keys())
    resolved: set[str] = set()
    order: list[Task] = []

    while remaining:
        candidates = [
            tid
            for tid in (t.task_id for t in tasks)
            if tid in remaining and not (dag[tid] - resolved)
        ]
        if not candidates:
            raise CycleDetectedError(list(remaining))
        chosen = candidates[0]
        order.append(task_map[chosen])
        resolved.add(chosen)
        remaining.remove(chosen)

    return order
