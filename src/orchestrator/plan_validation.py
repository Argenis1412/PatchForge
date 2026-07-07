"""Filesystem validation for architect plans (D-001 hardening)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.safety import ensure_safe_relative

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
