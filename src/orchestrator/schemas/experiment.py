from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from orchestrator.schemas.architect_output import ArchitectOutput

CURRENT_EXPERIMENT_SCHEMA_VERSION: int = 1


class Verdict(BaseModel):
    run_id: str
    status: Literal["passed", "failed"]
    validation_passed: bool
    apply_succeeded: bool
    error_message: str | None = None
    generated_at: datetime


class Experiment(BaseModel):
    run_id: str
    plan: ArchitectOutput
    target_commit_sha: str
    repository_identity: str
    workspace_path: Path
    schema_version: int = Field(default=CURRENT_EXPERIMENT_SCHEMA_VERSION)

    def verify(self, actual_commit_sha: str, actual_repo_identity: str) -> None:
        """Verify that the experiment execution context matches the runtime target environment.

        Raises ValueError if there is a mismatch.
        """
        from orchestrator.git import normalize_git_url

        if self.target_commit_sha != actual_commit_sha:
            raise ValueError(
                f"Commit SHA mismatch: experiment expected target commit {self.target_commit_sha}, "
                f"but target repository is at {actual_commit_sha}. Possible alignment risk."
            )

        norm_expected = normalize_git_url(self.repository_identity)
        norm_actual = normalize_git_url(actual_repo_identity)
        if norm_expected != norm_actual:
            raise ValueError(
                f"Repository identity mismatch: experiment expected repo "
                f"'{self.repository_identity}', but target repository is '{actual_repo_identity}'."
            )
