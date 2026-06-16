"""Experiment context: captures plan + target state for reproducibility verification."""

from __future__ import annotations

__all__ = [
    "CURRENT_EXPERIMENT_SCHEMA_VERSION",
    "Experiment",
    "Verdict",
    "verify_experiment_or_warn",
]

from datetime import datetime, timezone
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
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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


def verify_experiment_or_warn(workspace_mgr, run_id: str, target_path: Path) -> None:
    """Load experiment.json if present, and verify its context against target_path.

    Warns if experiment.json is missing.
    Raises ValueError if verification fails.
    """
    from rich.console import Console

    from orchestrator.git import current_head, repository_identity

    console = Console()
    try:
        experiment = workspace_mgr.read_experiment(run_id)
        experiment.verify(
            actual_commit_sha=current_head(target_path),
            actual_repo_identity=repository_identity(target_path),
        )
    except FileNotFoundError:
        console.print(
            "[yellow]Warning: experiment.json not found. "
            "Skipping strict commit/repository verification.[/yellow]"
        )
    except RuntimeError as exc:
        raise ValueError(f"Unable to verify experiment context: {exc}") from exc
