"""
schemas/executor_output.py
Contrato de salida del Executor. Define qué pasó con cada task.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FileChange(BaseModel):
    task_id: str
    file: str
    status: Literal["applied", "pending_human_review", "error"]
    diff: str | None = None          # unified diff antes/después
    original_content: str | None = None
    modified_content: str | None = None
    error: str | None = None         # mensaje si status == "error"
    tokens_used: int = 0
    cost_usd: float = 0.0


class ExecutorOutput(BaseModel):
    applied: list[FileChange] = Field(default_factory=list)          # LOW / MEDIUM
    pending_review: list[FileChange] = Field(default_factory=list)   # HIGH (diff generado, sin escribir a disco)
    errors: list[FileChange] = Field(default_factory=list)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    model: str = ""
    run_id: str = ""                 # timestamp ISO para correlacionar con logs
