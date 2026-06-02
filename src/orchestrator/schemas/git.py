from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


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
