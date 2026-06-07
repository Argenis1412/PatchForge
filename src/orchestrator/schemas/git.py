from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel


class ApplyCheckStatus(str, Enum):
    """Result categories for a dry-run git apply --check call."""

    # git apply --check exited with rc == 0.
    PASSED = "PASSED"
    # git apply --check exited with rc != 0 due to a merge conflict.
    CONFLICT = "CONFLICT"
    # git executable not found or the patch format caused a fatal process error.
    ERROR = "ERROR"


class GitCommandResult(BaseModel):
    return_code: int
    stdout: str
    stderr: str


class WorkingTreeStatus(BaseModel):
    is_clean: bool
    porcelain: str


class RepositoryState(BaseModel):
    root: Path
    head: str
    branch: str
    is_clean: bool


class ValidationWorkspace(BaseModel):
    original_root: Path
    temporary_root: Path
    patch_path: Path
