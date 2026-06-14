"""
schemas/executor_output.py
Output contract for the Executor agent. Defines what happened with each task.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    APPLIED = "applied"
    NOOP = "noop"
    SKIPPED = "skipped"
    ERROR = "error"
    PENDING_REVIEW = "pending_human_review"


class FileChange(BaseModel):
    task_id: str
    file: str
    status: TaskStatus
    diff: str | None = None  # unified diff before/after
    original_content: str | None = None
    modified_content: str | None = None
    error: str | None = None  # error message when status == "error"
    tokens_used: int = 0
    cost_usd: float = 0.0


class ExecutorOutput(BaseModel):
    applied: list[FileChange] = Field(default_factory=list)  # LOW / MEDIUM
    pending_review: list[FileChange] = Field(default_factory=list)  # HIGH risk (diff only)
    errors: list[FileChange] = Field(default_factory=list)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    model: str = ""
    run_id: str = ""  # ISO timestamp to correlate with logs
