"""Git rollback utility to reset repository to a target commit."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def rollback_to_commit(
    repo_root: Path, target_sha: str, backup_diff: Optional[Path] = None
) -> None:
    import subprocess

    from orchestrator.exceptions import RollbackError
    from orchestrator.git import force_reset_apply

    if backup_diff is not None and backup_diff.is_file():
        try:
            res = subprocess.run(
                ["git", "-C", str(repo_root), "apply", "--reverse", str(backup_diff)],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass  # fall through to force_reset_apply

    result = force_reset_apply(repo_root, target_sha)
    if result.return_code != 0:
        raise RollbackError(
            repo_root=repo_root,
            target_sha=target_sha,
            stderr=result.stderr,
        )
