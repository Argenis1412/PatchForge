"""Detect and log executor-side provider fallbacks (D-011d, Part 2).

A fallback is only reported when the task actually produced a deliverable
(applied, idempotent-noop, or handed to human review) — a fallback provider's
response that later failed syntax validation, or a fully exhausted chain,
must not be reported as "fell back, now using X" since nothing was delivered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from orchestrator.observability.events import log_event
from orchestrator.schemas.executor_output import ExecutorOutput, FileChange, TaskStatus

from .logging import _get_logger

_FALLBACK_ELIGIBLE_STATUSES = {TaskStatus.APPLIED, TaskStatus.NOOP, TaskStatus.PENDING_REVIEW}


def _is_fallback(change: FileChange) -> bool:
    return (
        change.status in _FALLBACK_ELIGIBLE_STATUSES
        and change.provider_name is not None
        and change.primary_provider_attempted is not None
        and change.provider_name != change.primary_provider_attempted
    )


def collect_fallback_changes(result: ExecutorOutput) -> list[FileChange]:
    """Return the FileChanges where a genuine, delivered fallback occurred."""
    all_changes = result.applied + result.pending_review + result.errors
    return [change for change in all_changes if _is_fallback(change)]


def log_fallback_events(
    fallback_changes: list[FileChange],
    run_id: str,
    trace_id: str,
    logs_dir: Path,
    run_dir: Optional[Path],
    level: str = "warning",
) -> None:
    """Persist one provider_fallback event per fallback change.

    Tolerates OSError the same way every other per-file event in this
    package does — a logging/disk failure must not crash an otherwise
    successful run.
    """
    for change in fallback_changes:
        try:
            log_event(
                trace_id=trace_id,
                run_id=run_id,
                level=level,
                source="executor",
                stage="executor",
                event="provider_fallback",
                data={
                    "task_id": change.task_id,
                    "file": change.file,
                    "primary_provider": change.primary_provider_attempted,
                    "used_provider": change.provider_name,
                    "category": change.primary_failure_category,
                },
                logs_dir=logs_dir,
                run_dir=run_dir,
            )
        except OSError as exc:
            _get_logger().warning(
                "[%s] Failed to emit provider_fallback event for task %s: %s",
                run_id,
                change.task_id,
                exc,
            )
