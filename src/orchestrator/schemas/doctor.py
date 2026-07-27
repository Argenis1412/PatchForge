"""Doctor check schemas: CheckStatus, CheckResult, DoctorResult models."""

from __future__ import annotations

__all__ = [
    "CheckResult",
    "CheckStatus",
    "DoctorResult",
]

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class CheckResult(BaseModel):
    name: str
    status: CheckStatus
    message: str
    detail: Optional[str] = None
    fix_hint: Optional[str] = None
    required: bool = True


class DoctorResult(BaseModel):
    target_path: str
    v1_supported: bool
    support_profile: Literal["v1", "v2", "unsupported"] = "unsupported"
    checks: list[CheckResult]
    workspace_path: Optional[str] = None
    git_branch: Optional[str] = None
    git_head: Optional[str] = None
    is_dirty: Optional[bool] = None
