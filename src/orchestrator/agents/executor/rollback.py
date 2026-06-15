from __future__ import annotations

from pathlib import Path


def rollback_to_commit(repo_root: Path, target_sha: str) -> None:
    from orchestrator.exceptions import RollbackError
    from orchestrator.git import force_reset_apply

    result = force_reset_apply(repo_root, target_sha)
    if result.return_code != 0:
        raise RollbackError(
            repo_root=repo_root,
            target_sha=target_sha,
            stderr=result.stderr,
        )
