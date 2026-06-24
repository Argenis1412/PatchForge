"""SQLite-backed work queue for at-least-once issue processing.

B8a: Work Queue Schema & Admission.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.storage import _sqlite_connect

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS issue_lock (
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    locked_until TEXT,
    PRIMARY KEY (repo, issue_number)
);

CREATE TABLE IF NOT EXISTS work_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_number INTEGER NOT NULL,
    repo TEXT NOT NULL,
    run_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    retries INTEGER DEFAULT 0,
    payload TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    scheduled_after TEXT,
    lease_expires_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_checkpoint (
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('scout','architect','executor','validator','apply')),
    output TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (run_id, stage)
);
"""


def init_queue_db(db_path: Path) -> sqlite3.Connection:
    conn = _sqlite_connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def _generate_run_id() -> str:
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(4)
    return f"run_{now}_{rand}"


def enqueue_issue(
    conn: sqlite3.Connection,
    issue_number: int,
    repo: str,
    payload: str,
) -> Optional[str]:
    run_id = _generate_run_id()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT INTO issue_lock (repo, issue_number, run_id, locked_until) "
            "VALUES (?, ?, ?, datetime('now', '+24 hours')) "
            "ON CONFLICT (repo, issue_number) DO UPDATE SET "
            "run_id = excluded.run_id, "
            "locked_until = excluded.locked_until "
            "WHERE locked_until < datetime('now')",
            (repo, issue_number, run_id),
        )
        if cur.rowcount == 0:
            conn.execute("ROLLBACK")
            return None
        conn.execute(
            "INSERT INTO work_queue (issue_number, repo, run_id, status, created_at, payload) "
            "VALUES (?, ?, ?, 'pending', datetime('now'), ?)",
            (issue_number, repo, run_id, payload),
        )
        conn.execute("COMMIT")
        return run_id
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        return None
    except Exception:
        conn.execute("ROLLBACK")
        raise


def dequeue_issue(conn: sqlite3.Connection) -> Optional[dict]:
    cur = conn.execute(
        "UPDATE work_queue SET "
        "status = 'processing', "
        "started_at = datetime('now'), "
        "lease_expires_at = datetime('now', '+1 hour'), "
        "retries = CASE WHEN status = 'processing' THEN retries + 1 ELSE retries END "
        "WHERE id = ("
        "SELECT id FROM work_queue "
        "WHERE status = 'pending' "
        "OR (status = 'processing' AND lease_expires_at <= datetime('now') AND retries < 3) "
        "ORDER BY created_at ASC LIMIT 1"
        ") "
        "RETURNING run_id, issue_number, repo, payload, retries"
    )
    row = cur.fetchone()
    return dict(row) if row else None
