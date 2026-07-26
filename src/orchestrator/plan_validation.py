"""Filesystem validation for architect plans (D-001 hardening)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.safety import ensure_safe_relative
from orchestrator.schemas.execution_plan import (
    ExecutablePlanV2,
    ExecutionPlanContractError,
    PlanViolation,
    task_fingerprint,
)

if TYPE_CHECKING:
    from orchestrator.schemas.architect_output import ArchitectOutput


def validate_plan_paths(plan: ArchitectOutput, target_path: Path) -> list[str]:
    """Check that every ``files_to_modify`` entry is reachable on disk.

    Returns a list of human-readable failure reasons (empty ⇒ pass).
    """
    reasons: list[str] = []
    empty_tasks: list[str] = []
    unsafe_paths: list[tuple[str, str, str]] = []
    phantom_paths: set[str] = set()
    case_mismatched: set[str] = set()

    for task in plan.implementation_plan:
        if not task.files_to_modify:
            empty_tasks.append(task.task_id)
            continue

        for rel_path in task.files_to_modify:
            try:
                ensure_safe_relative(rel_path, target_path)
            except ValueError as exc:
                unsafe_paths.append((task.task_id, rel_path, str(exc)))
                continue

            abs_path = target_path / rel_path

            if abs_path.exists():
                if os.name == "nt":
                    try:
                        names = [entry.name for entry in abs_path.parent.iterdir()]
                    except OSError:
                        names = []
                    if abs_path.name not in names:
                        case_mismatched.add(rel_path)
            else:
                if not abs_path.parent.exists():
                    phantom_paths.add(rel_path)

    if empty_tasks:
        ids = ", ".join(sorted(empty_tasks))
        reasons.append(f"Task(s) with empty files_to_modify: {ids}")

    for task_id, rel, msg in sorted(unsafe_paths):
        reasons.append(f"Task {task_id}: unsafe path {rel!r} — {msg}")

    if phantom_paths:
        paths = ", ".join(sorted(phantom_paths))
        reasons.append(f"Plan references non-existent paths (file and parent missing): {paths}")

    if case_mismatched:
        paths = ", ".join(sorted(case_mismatched))
        reasons.append(f"Plan references paths with case mismatch (Windows): {paths}")

    return reasons


def validate_execution_plan(plan: ExecutablePlanV2, target_path: Path) -> None:
    """Validate the deterministic V2 execution contract for ``target_path``.

    This is deliberately independent of the legacy architect proposal.  It is
    the single validation boundary for plans that already contain executable
    file mutations.
    """
    violations: list[PlanViolation] = []
    seen: set[str] = set()

    for task in plan.tasks:
        fingerprint = task_fingerprint(task)
        operation = task.operation
        path = operation.path

        if not path or path != path.strip():
            violations.append(
                PlanViolation(
                    task_fingerprint=fingerprint,
                    field="operation.path",
                    code="invalid_target",
                    message="operation.path must be a non-empty canonical relative path",
                    value=path,
                )
            )
            continue
        if (
            "\\" in path
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            violations.append(
                PlanViolation(
                    task_fingerprint=fingerprint,
                    field="operation.path",
                    code="noncanonical_target",
                    message="operation.path must use canonical relative POSIX syntax",
                    value=path,
                )
            )
            continue
        try:
            ensure_safe_relative(path, target_path)
        except ValueError as exc:
            violations.append(
                PlanViolation(
                    task_fingerprint=fingerprint,
                    field="operation.path",
                    code="unsafe_target",
                    message=str(exc),
                    value=path,
                )
            )
            continue

        normalized_path = path.casefold()
        if normalized_path in seen:
            violations.append(
                PlanViolation(
                    task_fingerprint=fingerprint,
                    field="operation.path",
                    code="duplicate_mutation_target",
                    message="each execution task must own a unique target path",
                    value=path,
                )
            )
        seen.add(normalized_path)

    if violations:
        raise ExecutionPlanContractError(violations)


def validate_legacy_executor_plan(plan: "ArchitectOutput", target_path: Path) -> None:
    """Reject legacy tasks that cannot produce any executor operation.

    Legacy proposals remain supported when they name concrete files.  A task
    with no target is the known analysis-only shape: the old executor would
    otherwise pass it to an LLM and cascade a failure through the DAG.
    """
    violations: list[PlanViolation] = []
    for task in plan.implementation_plan:
        if task.files_to_modify:
            continue
        violations.append(
            PlanViolation(
                task_fingerprint=task_fingerprint(task),
                field="files_to_modify",
                code="analysis_only_task",
                message=(
                    "legacy executor tasks require at least one file target; "
                    "move analysis into findings or blockers instead"
                ),
                value=task.task_id,
            )
        )
    if violations:
        raise ExecutionPlanContractError(violations)
