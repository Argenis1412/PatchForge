"""Storage helpers for PatchForge orchestrator.

B1: _wal_write — atomic crash-safe write for apply.json and other artifacts.
B4: _sqlite_connect — canonical SQLite connection (added later).
"""

__all__ = [
    "_wal_write",
]

import os
from pathlib import Path

from pydantic import BaseModel


def _wal_write(result: BaseModel, path: Path) -> None:
    """Atomic WAL write with guaranteed OS fsync. Call after EVERY status change."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))
        f.flush()  # flush Python buffer → OS buffer cache
        os.fsync(f.fileno())  # force OS buffer cache → physical disk
    os.replace(tmp, path)  # atomic rename (POSIX) / near-atomic (Windows)
    if os.name == "posix":
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)  # persist directory entry for crash-safe rename
        finally:
            os.close(dir_fd)
