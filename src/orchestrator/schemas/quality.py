"""Quality report schema for deterministic repository quality scanning.

These models describe the output of ``orchestrator.scanners.quality.scan``.
They are intentionally separate from :class:`~orchestrator.schemas.findings.ScanFindings`
(which answers "what is in the repo") so that the quality scanner
can evolve independently.
"""

from __future__ import annotations

__all__ = [
    "QualityCheck",
    "QualityDimension",
]

from pydantic import BaseModel


class QualityCheck(BaseModel):
    """A single deterministic quality check result."""

    id: str
    passed: bool
    score: int
    message: str


class QualityDimension(BaseModel):
    """A named dimension aggregating related checks."""

    name: str
    score: int
    checks: list[QualityCheck]
