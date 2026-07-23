"""Output contract of the Validator. Defines the result of each tool and the global summary."""

from __future__ import annotations

__all__ = [
    "CoverageStatus",
    "ExecutionState",
    "OverallStatus",
    "ToolResult",
    "ValidatorOutput",
]

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ExecutionState(str, Enum):
    APPROVED = "approved"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    NOT_RUN = "not_run"
    CLEANUP_FAILED = "cleanup_failed"


class CoverageStatus(str, Enum):
    VERIFIED = "verified"
    DECLARED_ONLY = "declared_only"
    ABSENT = "absent"


class OverallStatus(str, Enum):
    APPROVED = "approved"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


class ToolResult(BaseModel):
    """A legacy tool result or a V2 declaration result.

    V1 writers only populate the original fields. V2 execution adds declaration
    identity and terminal-state metadata while retaining a fail-closed `passed`
    projection for existing consumers.
    """

    tool: str
    passed: bool | None
    return_code: int
    stdout: str = ""
    stderr: str = ""
    error_summary: str | None = None  # Gemini fills this only if passed == False
    timed_out: bool = False
    validator_id: str | None = None
    adapter: str | None = None
    declaration_index: int | None = None
    status: ExecutionState | None = None
    declared_roles: list[str] = Field(default_factory=list)
    role_coverage: dict[str, CoverageStatus] = Field(default_factory=dict)


class ValidatorOutput(BaseModel):
    overall_passed: bool
    tools: list[ToolResult] = Field(default_factory=list)
    llm_summary: str | None = None  # global summary if at least one tool failed
    run_id: str = ""
    model_used_for_summary: str = ""  # empty if all passed (Gemini was not invoked)
    result_profile: Literal["v1", "v2"] | None = None
    overall_status: OverallStatus | None = None
