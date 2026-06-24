# B7 — Workspace Isolation & Repository Locking

## Goal

Prevent two workers from writing to the same staging directory or running `git apply` on the same clone simultaneously. Workers are isolated by `worker_id` in workspace paths; repo locking via SQLite is optional and disabled by default.

---

## Current State

### `src/orchestrator/workspace.py:25-46` — WorkspaceManager without isolation

```python
class WorkspaceManager:
    def __init__(self, workspace_path: Path):
        self.root = Path(workspace_path).resolve()
        self.runs = self.root / "runs"
        self.logs = self.root / "logs"
        self.prompts = self.root / "prompts"
        self.outputs = self.root / "outputs"
        self.cache = self.root / "cache"
        self.temp = self.root / "temp"
        self.manifest = self.outputs / "manifest.json"

    def setup(self) -> None:
        for directory in [
            self.root, self.runs, self.logs, self.prompts,
            self.outputs, self.cache, self.temp,
        ]:
            directory.mkdir(parents=True, exist_ok=True)
```

No `worker_id` scoping. All workers share the same directories.

### `src/orchestrator/workspace.py:48-51` — Shared staging under `outputs/staging/<run_id>/`

```python
def staging_dir_for_run(self, run_id: str) -> Path:
    path = self.outputs / "staging" / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path
```

Two workers hitting the same repo clone collide in staging writes.

---

## Changes

### 1. Incorporate `worker_id` into workspace paths

`src/orchestrator/workspace.py`:

```python
class WorkspaceManager:
    def __init__(self, workspace_path: Path, worker_id: str = ""):
        self.root = Path(workspace_path).resolve()
        if worker_id:
            self.root = self.root / worker_id  # e.g., .../workspaces/<hash>/worker-abc123
        self.runs = self.root / "runs"
        self.logs = self.root / "logs"
        self.prompts = self.root / "prompts"
        self.outputs = self.root / "outputs"
        self.cache = self.root / "cache"
        self.temp = self.root / "temp"
        self.manifest = self.outputs / "manifest.json"
        self._worker_id = worker_id
```

Workers are physically isolated by clone. Branches are unique per `run_id`/`issue_number`, so no branch conflicts across workers.

### 2. Optional repo lock using `coordination.db`

Create `repo_lock` table in `coordination.db` (same file as CB state from B4). Only enabled for shared-NFS deployments (`REPO_LOCK_ENABLED=False` by default).

**State of `lock.py` after B4:**
It contains `CircuitBreakerStore` and `SqliteCircuitBreakerStore`. Claude will need to append to this file. Do not overwrite the existing classes.

`src/orchestrator/storage/lock.py` (extend with):

```python
import time

def acquire_repo_lock(repo_identity: str, worker_id: str, ttl_seconds: int = 300, db_dir: Path = None) -> bool:
    # Use canonical _sqlite_connect() — never sqlite3.connect() directly.
    # See 00-README.md §Canonical Patterns
    conn = _sqlite_connect(db_dir / "coordination.db")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS repo_lock ("
        "repo TEXT PRIMARY KEY,"
        "worker_id TEXT NOT NULL,"
        "expires_at REAL NOT NULL)"
    )
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT worker_id, expires_at FROM repo_lock WHERE repo = ?",
            (repo_identity,)
        ).fetchone()
        now = time.time()
        if row:
            if now < row["expires_at"] and row["worker_id"] != worker_id:
                return False  # actively locked by another worker
        conn.execute(
            "INSERT OR REPLACE INTO repo_lock (repo, worker_id, expires_at) VALUES (?, ?, ?)",
            (repo_identity, worker_id, now + ttl_seconds)
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False

def release_repo_lock(repo_identity: str, worker_id: str, db_dir: Path) -> None:
    # Use canonical _sqlite_connect() — never sqlite3.connect() directly.
    conn = _sqlite_connect(db_dir / "coordination.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM repo_lock WHERE repo = ? AND worker_id = ?",
            (repo_identity, worker_id)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        pass  # If release fails, TTL will eventually expire it
```

`BEGIN IMMEDIATE` guarantees mutual exclusion across all workers — SQLite serializes write transactions at the process level.

### 3. Note on `pipeline_checkpoint` table

B8 creates `pipeline_checkpoint` in `queue.db`. B7's `coordination.db` does not include this table.

### 4. Stale lock TTL cleanup

`WorkspaceManager.cleanup_stale_workspaces(max_age_hours=24)`:

```python
def cleanup_stale_workspaces(self, max_age_hours: int = 24) -> None:
    """Remove worker subdirectories older than max_age_hours."""
    import time
    import shutil
    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    for child in self.root.parent.iterdir():
        if child.is_dir() and child.name.startswith("worker-"):
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
```

---

## Files to Modify/Create

- `src/orchestrator/workspace.py` — Accept `worker_id`, scope paths
- **NEW** `src/orchestrator/storage/lock.py` — `acquire_repo_lock()` function (if not already created by B4)
- `src/orchestrator/clients/bootstrap.py` — Ensure `repo_lock` table is created in `coordination.db`

---

## Acceptance Criteria

- [ ] Two workers never write to the same staging directory
- [ ] Repo lock prevents simultaneous `git apply` on the same clone — no TOCTOU race possible
- [ ] Stale locks are reaped transactionally (TTL check in `BEGIN IMMEDIATE`)
- [ ] Two stores for blast radius isolation: `coordination.db` (B4 cb_state + B7 repo_lock) and `queue.db` (B8 work_queue + pipeline_checkpoint + issue_lock). A corruption in one never blocks the other.
- [ ] Repo locking is **optional** and **disabled by default** (`REPO_LOCK_ENABLED=False`)

---

## Test skeleton (create before running pytest)

Create `tests/test_workspace.py` with these cases:
```python
def test_workspace_isolation():
    """Verify WorkspaceManager instances use unique paths based on worker_id."""
    pass

def test_stale_workspace_cleanup():
    """Verify cleanup_stale_workspaces removes only old worker directories."""
    pass
```

Create `tests/test_git_safety.py` with these cases:
```python
def test_repo_lock_mutual_exclusion():
    """Verify acquire_repo_lock prevents concurrent access."""
    pass

def test_stale_lock_ttl_cleanup():
    """Verify expired locks are reaped."""
    pass
```

## Verification

```bash
pytest tests/test_workspace.py -v
pytest tests/test_git_safety.py -v

# Manual: simulate two workers
WORKER_ID=worker-1 python -c "
from orchestrator.workspace import WorkspaceManager
w = WorkspaceManager('/tmp/test-workspace', worker_id='worker-1')
assert 'worker-1' in str(w.root)
print('Worker isolation OK')
"
```

## Rollback

```bash
git checkout -- src/orchestrator/workspace.py
git checkout -- src/orchestrator/storage/lock.py  # existing from B4
```
