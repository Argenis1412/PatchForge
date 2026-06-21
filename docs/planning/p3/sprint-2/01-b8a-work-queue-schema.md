# B8a — Work Queue Schema & Admission

## Goal

Provide a persistent work queue so issues survive worker crashes and are guaranteed at-least-once delivery. Define the schema and implement `enqueue_issue` / `dequeue_issue`.

---

## Current State

### No storage layer exists

No `queue.db`, no `issue_lock`, no `work_queue` table. Everything is single-process local filesystem.

---

## Changes

### 1. Create `src/orchestrator/storage/work_queue.py`

```python
"""Queue, pipeline_checkpoint, and issue_lock tables in queue.db."""

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
    lease_expires_at TEXT,         -- Lease expiration for visibility timeout
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

# Residual Risk Note: This visibility timeout implementation handles silent worker crashes.
# However, it does not use a heartbeat. If a task takes longer than 1 hour (unlikely under normal timeouts),
# a second worker might reclaim it, resulting in a split-brain execution. This is a known/documented risk for P3.

def init_queue_db(db_path: Path) -> sqlite3.Connection:
    # Use canonical _sqlite_connect() — never sqlite3.connect() directly.
    # See 00-README.md §Canonical Patterns
    conn = _sqlite_connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn

def enqueue_issue(conn: sqlite3.Connection, issue_number: int, repo: str, payload: str) -> Optional[str]:
    """Admission idempotency via issue_lock + enqueue in one ACID transaction."""
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO issue_lock (repo, issue_number, run_id) VALUES (?, ?, ?)",
            (repo, issue_number, run_id)
        )
        conn.execute(
            "INSERT INTO work_queue (run_id, issue_number, repo, status, created_at, payload) "
            "VALUES (?, ?, ?, 'pending', datetime('now'), ?)",
            (run_id, issue_number, repo, payload)
        )
        conn.commit()
        return run_id
    except sqlite3.IntegrityError:
        conn.rollback()
        return None                         # duplicate — discard silently
    except Exception:
        conn.rollback()
        raise                               # propagates OperationalError up

def dequeue_issue(conn: sqlite3.Connection) -> Optional[dict]:
    """Atomically claim the oldest pending or lease-expired issue."""
    row = conn.execute("""
        UPDATE work_queue 
        SET status = 'processing', 
            started_at = datetime('now'),
            lease_expires_at = datetime('now', '+1 hour')
        WHERE id = (
            SELECT id FROM work_queue
            WHERE status = 'pending'
               OR (status = 'processing' AND lease_expires_at <= datetime('now'))
            ORDER BY created_at ASC
            LIMIT 1
        )
        RETURNING run_id, issue_number, repo, payload, retries
    """).fetchone()
    if row:
        return dict(row)
    return None
```

---

## Files to Create/Modify

- **NEW** `src/orchestrator/storage/work_queue.py` — Queue schema + enqueue/dequeue
- `src/orchestrator/clients/bootstrap.py` — Init `queue.db` alongside `coordination.db`

---

## Acceptance Criteria

- [ ] Duplicate webhook delivery → `IntegrityError` on `issue_lock` → discard
- [ ] Queue is observable via SQL queries

---

## Test skeleton (create before running pytest)

Create `tests/test_work_queue.py` with these cases:
```python
def test_enqueue_issue_idempotency():
    """Verify duplicate enqueues return None and don't insert duplicate work items."""
    pass

def test_dequeue_issue_fifo():
    """Verify dequeue returns oldest pending issue."""
    pass
```

## Verification

```bash
pytest tests/test_work_queue.py -v

python -c "
from orchestrator.storage.work_queue import init_queue_db, enqueue_issue, dequeue_issue
from pathlib import Path
conn = init_queue_db(Path('/tmp/queue-test.db'))
run_id = enqueue_issue(conn, 42, 'owner/repo', '{\"title\": \"test\"}')
assert run_id is not None, 'enqueue failed'
row = dequeue_issue(conn)
assert row is not None and row['issue_number'] == 42, 'dequeue failed'
print('Queue OK')
"
```

## Rollback

```bash
git rm src/orchestrator/storage/work_queue.py
git checkout -- src/orchestrator/clients/bootstrap.py
```
