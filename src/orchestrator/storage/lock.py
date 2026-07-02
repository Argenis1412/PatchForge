"""Circuit Breaker storage backend.

B4: CircuitBreakerStore ABC + SqliteCircuitBreakerStore (coordination.db).
     _InMemoryCircuitBreakerStore is the in-process fallback used by circuit_breaker_for().
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from orchestrator.storage import _sqlite_connect

_logger = logging.getLogger(__name__)

_REPO_LOCK_ENABLED = os.environ.get("REPO_LOCK_ENABLED", "1") == "1"
_WORKER_ID: str = os.environ.get("WORKER_ID", "unknown")


class CircuitBreakerStore(ABC):
    @abstractmethod
    def get_state(self, provider: str) -> dict | None: ...

    @abstractmethod
    def set_state(self, provider: str, state: dict) -> None: ...

    @abstractmethod
    def atomic_update(self, provider: str, txn: Callable[[dict], dict]) -> dict: ...


class SqliteCircuitBreakerStore(CircuitBreakerStore):
    """Shared SQLite-backed store. State persists across worker restarts."""

    def __init__(self, db_dir: Path) -> None:
        db_dir.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = _sqlite_connect(db_dir / "coordination.db")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cb_state ("
            "provider TEXT PRIMARY KEY,"
            "state TEXT NOT NULL,"
            "failures INTEGER DEFAULT 0,"
            "last_failure_at REAL,"
            "recovery_timeout REAL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS half_open_probe ("
            "provider TEXT PRIMARY KEY,"
            "worker_id TEXT NOT NULL,"
            "acquired_at TEXT NOT NULL)"
        )

    def get_state(self, provider: str) -> dict | None:
        row = self._conn.execute(
            "SELECT state, failures, last_failure_at, recovery_timeout "
            "FROM cb_state WHERE provider = ?",
            (provider,),
        ).fetchone()
        return dict(row) if row else None

    def set_state(self, provider: str, state: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cb_state "
            "(provider, state, failures, last_failure_at, recovery_timeout) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                provider,
                state["state"],
                state.get("failures", 0),
                state.get("last_failure_at"),
                state.get("recovery_timeout"),
            ),
        )

    def atomic_update(self, provider: str, txn: Callable[[dict], dict]) -> dict:
        """Run txn(current_state) → new_state atomically. Re-raises on any error."""
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT state, failures, last_failure_at, recovery_timeout "
                "FROM cb_state WHERE provider = ?",
                (provider,),
            ).fetchone()
            new_state = txn(dict(row) if row else {})
            self._conn.execute(
                "INSERT OR REPLACE INTO cb_state "
                "(provider, state, failures, last_failure_at, recovery_timeout) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    provider,
                    new_state["state"],
                    new_state.get("failures", 0),
                    new_state.get("last_failure_at"),
                    new_state.get("recovery_timeout"),
                ),
            )
            self._conn.execute("COMMIT")
            return new_state
        except Exception:
            self._conn.execute("ROLLBACK")
            raise


class _InMemoryCircuitBreakerStore(CircuitBreakerStore):
    """In-process fallback. State is NOT shared across workers or restarts."""

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}

    def get_state(self, provider: str) -> dict | None:
        return self._data.get(provider)

    def set_state(self, provider: str, state: dict) -> None:
        self._data[provider] = state.copy()

    def atomic_update(self, provider: str, txn: Callable[[dict], dict]) -> dict:
        current = self._data.get(provider, {})
        new_state = txn(current)
        self._data[provider] = new_state
        return new_state


def acquire_repo_lock(
    repo_identity: str, worker_id: str, ttl_seconds: int = 300, db_dir: Path | None = None
) -> bool:
    if db_dir is None:
        return False
    try:
        conn = _sqlite_connect(db_dir / "coordination.db")
    except Exception:
        return False
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS repo_lock ("
            "repo TEXT PRIMARY KEY,"
            "worker_id TEXT NOT NULL,"
            "expires_at REAL NOT NULL)"
        )
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT worker_id, expires_at FROM repo_lock WHERE repo = ?",
            (repo_identity,),
        ).fetchone()
        now = time.time()
        if row and now < row["expires_at"] and row["worker_id"] != worker_id:
            conn.rollback()
            return False
        conn.execute(
            "INSERT OR REPLACE INTO repo_lock (repo, worker_id, expires_at) VALUES (?, ?, ?)",
            (repo_identity, worker_id, now + ttl_seconds),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def release_repo_lock(repo_identity: str, worker_id: str, db_dir: Path) -> None:
    try:
        conn = _sqlite_connect(db_dir / "coordination.db")
    except Exception:
        _logger.warning(
            "release_repo_lock: failed to connect for repo %r worker %r",
            repo_identity,
            worker_id,
            exc_info=True,
        )
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM repo_lock WHERE repo = ? AND worker_id = ?",
            (repo_identity, worker_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        _logger.warning(
            "release_repo_lock: failed to delete lock for repo %r worker %r",
            repo_identity,
            worker_id,
            exc_info=True,
        )
    finally:
        conn.close()
