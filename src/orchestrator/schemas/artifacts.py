import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional
from pydantic import BaseModel, Field

RUN_JSON = "run.json"
FINDINGS_JSON = "findings.json"
PLAN_JSON = "plan.json"
PATCH_DIFF = "patch.diff"
VALIDATION_JSON = "validation.json"
EVENTS_JSONL = "events.jsonl"
APPLY_JSON = "apply.json"
POST_APPLY_VALIDATION_JSON = "post_apply_validation.json"


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    v1_supported: bool
    support_reasons: List[str] = Field(default_factory=list)
    risk_budget: str = "medium"
    max_files: int = 5
    max_diff_lines: int = 500

    # Fields updated later by other commands
    goal: Optional[str] = None
    affected_files: Optional[List[str]] = None
    patch_checksum: Optional[str] = None
    validation_summary: Optional[str] = None
    model_metadata: Optional[dict[str, Any]] = None
    lifecycle_state: Optional[str] = None
    apply_status: Optional[str] = None
    failure_artifacts: Optional[List[str]] = None
