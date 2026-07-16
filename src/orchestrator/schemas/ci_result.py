"""CI pipeline result: thin projection of RunMetadata for machine consumption."""

from __future__ import annotations

__all__ = ["CiResult"]

from typing import List, Literal, Optional

from pydantic import BaseModel


class CiResult(BaseModel):
    run_id: str
    branch: str
    status: Literal[
        "applied",
        "scan_failed",
        "plan_failed",
        "preview_failed",
        "apply_failed",
    ]
    risk_budget: str
    affected_files: List[str]
    validation_passed: bool
    error: Optional[str] = None
    issue_number: Optional[int] = None
    force_provider: Optional[str] = None
    triggered_by: Optional[str] = None
    approved_by: Optional[str] = None
