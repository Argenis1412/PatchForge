import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class PatchLifecycleState(str, Enum):
    """Lifecycle classification for a generated patch."""

    # HEAD matches base_commit and git apply --check passes.
    VALID = "VALID"
    # HEAD diverged from base_commit but git apply --check still passes.
    REBASEABLE = "REBASEABLE"
    # git apply --check fails with a merge conflict.
    CONFLICT = "CONFLICT"
    # patch.diff is missing/empty, or git apply --check fails due to a process
    # error (git not found, invalid patch format, etc.).
    STALE = "STALE"


RUN_JSON = "run.json"
FINDINGS_JSON = "findings.json"
PLAN_JSON = "plan.json"
PATCH_DIFF = "patch.diff"
VALIDATION_JSON = "validation.json"
EVENTS_JSONL = "events.jsonl"
APPLY_JSON = "apply.json"
POST_APPLY_VALIDATION_JSON = "post_apply_validation.json"
ISSUE_MD = "issue.md"
EXPERIMENT_JSON = "experiment.json"


CURRENT_SCHEMA_VERSION: int = 1


def generate_run_id() -> str:
    now = datetime.now(timezone.utc)
    short = uuid.uuid4().hex[:6]
    return f"run_{now.strftime('%Y%m%d_%H%M%S')}_{short}"


class RunMetadata(BaseModel):
    # Initial fields created by scan
    run_id: str
    target_path: str
    workspace_path: str
    base_commit: str
    branch: str
    status: str = "scanning"
    schema_version: int = CURRENT_SCHEMA_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    v1_supported: bool
    support_reasons: List[str] = Field(default_factory=list)
    risk_budget: Literal["low", "medium", "high"] = "low"
    max_files: int = Field(default=2, ge=1)
    max_diff_lines: int = Field(default=100, ge=1)

    # Fields updated later by other commands
    goal: Optional[str] = None
    affected_files: Optional[List[str]] = None
    patch_checksum: Optional[str] = None
    validation_summary: Optional[str] = None
    model_metadata: Optional[dict[str, Any]] = None
    lifecycle_state: Optional[PatchLifecycleState] = None
    apply_status: Optional[str] = None
    failure_artifacts: Optional[List[str]] = None


class ApplyResult(BaseModel):
    run_id: str
    applied_at: datetime
    branch: str
    success: bool
    rolled_back: bool = False
    error: Optional[str] = None
    pre_apply_head: Optional[str] = None
    pre_apply_branch: Optional[str] = None
    rollback_head: Optional[str] = None
