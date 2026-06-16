"""Legacy pipeline run schema. Tracks per-agent metadata and task results."""

__all__ = [
    "AgentMeta",
    "PipelineRun",
    "TaskResult",
]

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class AgentMeta(BaseModel):
    status: str
    latency_ms: int
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    cost_usd: Optional[float] = None
    model_used: Optional[str] = None
    error: Optional[str] = None


class TaskResult(BaseModel):
    task_id: str
    status: str
    risk_level: str
    model_used: str
    error: Optional[str] = None


class PipelineRun(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_path: str
    status: str = "pending"
    scout_meta: Optional[AgentMeta] = None
    architect_meta: Optional[AgentMeta] = None
    executor_meta: Optional[AgentMeta] = None
    validator_meta: Optional[AgentMeta] = None
    tasks_total: int = 0
    tasks_applied: int = 0
    tasks_failed: int = 0
    tasks_pending_review: int = 0
    task_results: List[TaskResult] = []
    pending_human_review: List[str] = []
    total_cost_usd: float = 0.0
    finished_at: Optional[datetime] = None
