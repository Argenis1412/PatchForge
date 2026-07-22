"""Detect and log architect-side provider fallbacks (D-011d, Parts 1 & 3).

Shared by commands/plan.py and commands/ci.py so both entrypoints report the
same event shape without duplicating arch_meta field-extraction logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from orchestrator.observability.events import log_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArchitectFallback:
    primary_provider_attempted: str
    provider_used: Optional[str]
    failure_category: Optional[str]


def detect_fallback(arch_meta: dict) -> Optional[ArchitectFallback]:
    """Return fallback info if the architect's provider chain fell back, else None."""
    primary_attempted = arch_meta.get("primary_provider_attempted")
    provider_used = arch_meta.get("provider_name")
    if primary_attempted is None or primary_attempted == provider_used:
        return None
    return ArchitectFallback(
        primary_provider_attempted=primary_attempted,
        provider_used=provider_used,
        failure_category=arch_meta.get("primary_failure_category"),
    )


def log_architect_fallback(
    fallback: ArchitectFallback,
    *,
    run_id: str,
    trace_id: str,
    source: str,
    logs_dir: Path,
    run_dir: Optional[Path],
    level: str = "warning",
) -> None:
    """Persist a provider_fallback event for the architect stage.

    Tolerates OSError the same way the executor's log_fallback_events does —
    a logging/disk failure must not crash an otherwise successful run.
    """
    try:
        log_event(
            trace_id=trace_id,
            run_id=run_id,
            level=level,
            source=source,
            stage="architect",
            event="provider_fallback",
            data={
                "primary_provider": fallback.primary_provider_attempted,
                "used_provider": fallback.provider_used,
                "category": fallback.failure_category,
            },
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
    except OSError as exc:
        logger.warning("Failed to emit provider_fallback event for architect: %s", exc)
