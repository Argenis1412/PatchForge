"""Storage helpers for PatchForge orchestrator.

B1: _wal_write — atomic crash-safe write for apply.json and other artifacts.
B4: _sqlite_connect — canonical SQLite connection factory.
B5: ArtifactStore — pluggable artifact backend with LocalArtifactStore.
"""

__all__ = [
    "_wal_write",
    "_sqlite_connect",
    "ArtifactStore",
    "DurabilityLevel",
    "WriteResult",
    "LocalArtifactStore",
]

import os
import sqlite3
from pathlib import Path

from pydantic import BaseModel

from .artifact_store import ArtifactStore, DurabilityLevel, WriteResult
from .local_store import LocalArtifactStore


def _sqlite_connect(db_path: Path, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Canonical SQLite connection factory. Always enables WAL mode and autocommit.
    Never call sqlite3.connect() directly — always use this factory."""
    conn = sqlite3.connect(str(db_path), timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _wal_write(result: BaseModel, path: Path) -> None:
    """Atomic WAL write with guaranteed OS fsync. Call after EVERY status change."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
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
