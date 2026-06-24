from pathlib import Path

import pytest

from orchestrator.storage.work_queue import dequeue_issue, enqueue_issue, init_queue_db


@pytest.fixture
def qdb(tmp_path: Path):
    db_path = tmp_path / "queue.db"
    conn = init_queue_db(db_path)
    yield conn
    conn.close()


def test_enqueue_issue_idempotency(qdb):
    run_id = enqueue_issue(qdb, 42, "owner/repo", '{"title": "test"}')
    assert run_id is not None
    dup = enqueue_issue(qdb, 42, "owner/repo", '{"title": "duplicate"}')
    assert dup is None
    rows = qdb.execute("SELECT COUNT(*) as cnt FROM work_queue").fetchone()
    assert rows["cnt"] == 1


def test_dequeue_issue_fifo(qdb):
    enqueue_issue(qdb, 1, "a/b", "1")
    enqueue_issue(qdb, 2, "a/b", "2")
    first = dequeue_issue(qdb)
    assert first is not None and first["issue_number"] == 1
    second = dequeue_issue(qdb)
    assert second is not None and second["issue_number"] == 2
    third = dequeue_issue(qdb)
    assert third is None


def test_dequeue_empty(qdb):
    assert dequeue_issue(qdb) is None


def test_dequeue_lease_expiry(qdb):
    enqueue_issue(qdb, 1, "a/b", "payload")
    first = dequeue_issue(qdb)
    assert first is not None and first["retries"] == 0
    assert first["issue_number"] == 1
    sim_id = qdb.execute("SELECT id FROM work_queue WHERE issue_number = 1").fetchone()["id"]
    qdb.execute(
        "UPDATE work_queue SET lease_expires_at = datetime('now', '-1 minute') WHERE id = ?",
        (sim_id,),
    )
    second = dequeue_issue(qdb)
    assert second is not None and second["issue_number"] == 1
    assert second["retries"] == 1
