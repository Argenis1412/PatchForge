# P3 — Patches to Inject per Blocker

**Purpose:** Paste the relevant sections of this file **alongside** each original blocker `.md` when starting a Claude Code Pro session. Does not rewrite the blockers whole — only patches what breaks at runtime.

---

## Table of Contents

| Section | Use in session |
|---------|----------------|
| §1 Corrected Order | **ALL** sessions |
| §2 Fix B4 (Circuit Breaker) | B4 |
| §3 Fix B8b (Worker Loop) | B8b |
| §4 Prerequisites Moved Before B8b | B3, B5 (before B8b) |
| §5 Fix B3 (GitHub) | B3 |
| §6 Canonical Patterns (literal code) | **ALL** sessions |
| §7 Accepted Risks V1 | Documentation only |

---

## §1 — Corrected Implementation Order

**Reason:** The original order (`B6→B1→B2→B4→B7→B8a→B8b→B3→B5`) places B8b before B3 and B5, but `worker_loop()` uses `ArtifactStore` and `GitHubClient` as types and calls `store.read()`, `github.get_pr_for_branch()` — classes that don't exist until B3 and B5, so B8b must come after them.

```text
Sprint 0   B6 → B1 → B2
Sprint 1   B4 → B7
Sprint 2   B8a → B3 → B5 → B8b → (post-audit-fixes.md)
```

**Dependency justification:**
| Dependency | Why |
|-------------|---------|
| `B4 → B7` | B7 extends `lock.py` which B4 creates |
| `B4 → B8a` | B8a uses `_sqlite_connect()` which B4 creates in `storage/__init__.py` |
| `B8a → B3` | B3's `webhook.py` imports `enqueue_issue`/`init_queue_db` from `work_queue.py` |
| `B3 → B8b` | B8b's `worker_loop` receives `GitHubClient`, calls `get_pr_for_branch()` |
| `B5 → B8b` | B8b's `worker_loop` receives `ArtifactStore`, calls `store.read()` |
| `B8b → post-audit` | post-audit patches code that only exists after B8b |

---

## §2 — Fix for B4 (Circuit Breaker) — IMPLEMENTATION NOTE

**The original B4 spec defined `_call_with_half_open_probe` as the production gatekeeper. This was discarded after audit. The actual implementation (commits `e81fafe`, `ac978c7`) takes a different approach:**

### 2.1 `CircuitBreaker.call()` with `SqliteCircuitBreakerStore` is the sole gatekeeper

**Decision:** `CircuitBreaker.call()` is the production entry point, backed by `SqliteCircuitBreakerStore` injected into `circuit_breaker_for()`. `_call_with_half_open_probe`, `ProbeSlotBusyError`, `_release_probe_token`, and `_cleanup_stale_probes` were **removed** — they never existed in the final codebase.

**Key design:**
- `CircuitBreaker` accepts `store: CircuitBreakerStore` parameter
- `_load_state()` reads from store at init; `_persist_state()` writes on every mutation
- `_reload_state()` called at start of `call()` — picks up cross-worker state changes
- `time.time()` replaces `time.monotonic()` for restart-safe comparison
- In-process `_half_open_in_flight` prevents double-probe within same worker
- Cross-worker HALF_OPEN contention is NOT prevented (accepted relaxation)

### 2.2 No separate store file

The `CircuitBreakerStore` ABC and `SqliteCircuitBreakerStore` live in `src/orchestrator/storage/lock.py`. The file `circuit_breaker_store.py` was **never created**. Ignore all `04-post-audit-fixes.md` references to it.

### 2.3 No `ProbeSlotBusyError` needed

Since `_call_with_half_open_probe` was removed, `ProbeSlotBusyError` was never added to `exceptions.py`. `CircuitBreakerOpenError` is the only CB exception in production code.

---

## §3 — Fix for B8b (Worker Loop)

### 3.1 Fix: `row` out of scope (line 178)

In `_execute_pipeline_with_resume()`:

```python
# WRONG:
_execute_apply_with_checkpoints(run_id, conn, github, workspace, issue_number=row.get("issue_number"))

# CORRECT:
_execute_apply_with_checkpoints(run_id, conn, github, workspace, issue_number=issue_number)
```

**Note:** `issue_number` is already a parameter of `_execute_pipeline_with_resume`. Don't look it up from `row`.

### 3.2 Fix: `coordination_db_dir` undefined (line ~235)

In `_execute_apply_with_checkpoints()`:

```python
# WRONG:
execute_apply(run_id=run_id, workspace=repo_path,
              issue_number=issue_number,
              worker_id=os.environ.get("WORKER_ID"),
              coordination_db_dir=coordination_db_dir)

# CORRECT: Add coordination_db_dir as a function parameter
def _execute_apply_with_checkpoints(run_id, conn, github, workspace,
                                    coordination_db_dir=None, issue_number=None):
    ...
    execute_apply(run_id=run_id, workspace=repo_path,
                  issue_number=issue_number,
                  worker_id=os.environ.get("WORKER_ID"),
                  coordination_db_dir=coordination_db_dir)
```

And in `_execute_pipeline_with_resume()` pass `coordination_db_dir` when calling:

```python
_execute_apply_with_checkpoints(run_id, conn, github, workspace,
                                coordination_db_dir=coord_db,  # the coord_db param the function receives
                                issue_number=issue_number)
```

### 3.3 Fix: `repo_path = workspace.root` is the wrong directory

In `_execute_apply_with_checkpoints()`:

```python
# WRONG:
repo_path = workspace.root  # this is staging/runs/logs, NOT the git repo

# CORRECT:
# Read from run_metadata.target_path
from orchestrator.schemas.artifacts import RunMetadata
run_meta = RunMetadata.model_validate_json(
    (workspace.run_dir(run_id) / "run.json").read_text()
)
repo_path = Path(run_meta.target_path)
```

Or, if `target_path` is not available yet at that point, pass it as a parameter from `_execute_pipeline_with_resume` which already has `payload_dict`:

```python
def _execute_pipeline_with_resume(run_id, payload, issue_number, conn, conn_coord, workspace, github, store):
    payload_dict = json.loads(payload)
    ...
    _execute_apply_with_checkpoints(run_id, conn, github, workspace,
                                    coordination_db_dir=coord_db,
                                    issue_number=issue_number,
                                    repo_path=repo_path_derived)
```

### 3.4 Fix: `sha256(patch)` without encode + without import

In `_execute_pipeline_with_resume()`, the executor checkpoint:

```python
# WRONG:
json.dumps({"ref": ref, "checksum": sha256(patch).hexdigest()})

# CORRECT: add import at top
import hashlib
# and use:
json.dumps({"ref": ref, "checksum": hashlib.sha256(patch.encode("utf-8")).hexdigest()})
```

### 3.5 Fix: `_ensure_clone` needs repo_url from payload

In `_execute_pipeline_with_resume()`:

```python
# B8b writes:
_ensure_clone(payload_dict, workspace)

# But _ensure_clone() expects payload_dict["repo_url"] which comes from the webhook.
# If payload_dict has no "repo_url" (e.g. direct CLI), it fails.
# Add guard:
def _ensure_clone(payload_dict, workspace):
    repo_url = payload_dict.get("repo_url")
    if not repo_url:
        repo_path = workspace.root / "repo"
        if not (repo_path / ".git").exists():
            raise ValueError("repo_url is required unless the repo is already cloned at workspace.root/repo")
        return repo_path
    ...
```

---

## §4 — Prerequisites Moved Before B8b

### 4.1 B5 (ArtifactStore) must be implemented before B8b

B5 defines `ArtifactStore`, `LocalArtifactStore`, `WriteResult`, `DurabilityLevel`. B8b needs:
- `store.read(f"{run_id}/run.json")` in `_hydrate_workspace()`
- `store.read(f"{run_id}/risk_gate.json")` in `_hydrate_workspace()`
- `store.write(f"{run_id}/patch.diff", patch)` in executor checkpoint

**When implementing B5 without B8b present,** ensure `WorkspaceManager.write_artifact()` delegates to the configured store (as B5 §5 says). `LocalArtifactStore` must be functional before B8b attempts `store.read()`.

### 4.2 B3 (GitHubClient) must be implemented before B8b

B8b needs:
- `GitHubClient.get_pr_for_branch(branch)` for idempotency check
- `GitHubClient.create_pr(...)` and `GitHubClient.close_pr(...)` in the apply pipeline

Also, see §5 below for the `_with_retry` fix in B3.

---

## §5 — Fix for B3 (GitHubClient)

### 5.1 `_with_retry` is never defined in the class body

**Bug:** B3 defines methods `create_pr`, `close_pr`, `comment_on_issue`, `add_label` and all call `self._with_retry(...)` but the `_with_retry` method **never appears** in `GitHubClient` code — it only exists as a comment "Use canonical _with_retry()" (line 100-101). Guaranteed `AttributeError`.

**Fix:** Add the method to the `GitHubClient` class:

```python
def _with_retry(self, fn, *args, max_retries: int = 3, **kwargs):
    """Retry wrapper with jitter on rate limit. Canonical pattern."""
    import random, time
    from github import GithubException
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except GithubException as e:
            if e.status == 403 and "rate limit" in str(e).lower():
                wait = int(e.headers.get("Retry-After", 60))
                time.sleep(wait + random.uniform(0, 5))
                continue
            raise
    raise RuntimeError(f"Max retries ({max_retries}) exceeded on rate limit")
```

### 5.2 Duplicate import (M-2)

B3 lines 39-41 have:

```python
from typing import List, Optional
from typing import Any, List, Optional  # DUPLICATE
```

Remove line 41:

```python
from typing import Any, List, Optional
```

### 5.3 ENV var check for token (M-6)

Add at the start of `GitHubClient.__init__()`:

```python
def __init__(self, token: str, repo: str):
    if not token:
        import os
        token = os.environ.get("PATCHFORGE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise RuntimeError(
                "PATCHFORGE_GITHUB_TOKEN or GITHUB_TOKEN must be set"
            )
    ...
```

---

## §6 — Canonical Patterns (literal code to paste in each session)

### `_wal_write` (WAL atomic write)

```python
import os
from pathlib import Path
from pydantic import BaseModel

def _wal_write(result: BaseModel, path: Path) -> None:
    """Atomic WAL write with guaranteed OS fsync. Call after EVERY status change."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    if os.name == "posix":
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
```

Location: `src/orchestrator/storage/__init__.py` (created by B4).

### `_sqlite_connect` (Safe DB connection)

```python
import sqlite3
from pathlib import Path

def _sqlite_connect(db_path: Path, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Canonical SQLite connection with WAL mode and IMMEDIATE locking."""
    conn = sqlite3.connect(str(db_path), timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn
```

Location: `src/orchestrator/storage/__init__.py`.

### `_with_retry` (GitHub API resilience)

```python
import random
import time
from github import GithubException

def _with_retry(self, fn, *args, max_retries: int = 3, **kwargs):
    """Retry wrapper for GitHub API calls with jitter on rate limit."""
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except GithubException as e:
            if e.status == 403 and "rate limit" in str(e).lower():
                wait = int(e.headers.get("Retry-After", 60))
                time.sleep(wait + random.uniform(0, 5))
                continue
            raise
    raise RuntimeError(f"Max retries ({max_retries}) exceeded on rate limit")
```

Location: method of `GitHubClient` in `src/orchestrator/clients/github.py`.

---

## §7 — Accepted Risks for V1 (non-blocking, documented)

### 7.1 Two CB mechanisms not reconciled (architectural) — RESOLVED

`_call_with_half_open_probe()` was **removed** in e81fafe. `CircuitBreaker.call()` with `SqliteCircuitBreakerStore` is the **sole** mechanism for both production and tests. There is no dual-path conflict.

**Impact on test coverage:** All 13 tests in `test_circuit_breaker.py` exercise the real `CircuitBreaker` class with either `MagicMock` (in-memory unit tests) or `SqliteCircuitBreakerStore` (integration tests 11-13). Both paths use the same production code.

### 7.2 `ARTIFACT_MAP` never used in worker recovery

Defined in b8b but only `executor` checkpoint writes to `ArtifactStore`. `findings.json`, `plan.json`, `validation.json` exist only in the `pipeline_checkpoint.output` SQLite column, not in the remote store. If a worker crashes and resumes in another container with a remote backend (S3), those files don't exist on disk.

**Accepted for V1** because intra-worker recovery (same container) works: `_hydrate_workspace()` and `_hydrate_stage()` rematerialize from the local SQLite checkpoint.

### 7.3 No HMAC verification on webhook

`handle_issue_opened()` accepts any payload without verifying `X-Hub-Signature-256`. Anyone hitting the endpoint can enqueue fake issues.

**Accepted for V1** because:
- The endpoint would sit behind an API gateway/auth in production
- `issue_lock` prevents duplicates but not fake issues
- Damage is bounded: consumes LLM budget and opens spurious PRs

**TODO (post-P3):** Add `verify_hmac(event, secret)` in `integrations/webhook.py`.

### 7.4 `_pre_dequeue_backpressure` blocks everything if any provider is OPEN

If Gemini is OPEN but the next queued item uses only Claude, the worker still waits. This is intentional for V1 (simplicity) but sub-optimal.

**Accepted:** Future improvement would be per-item provider check (requires provider routing schema in payload).

### 7.5 `BACKOFF_MINUTES[2] = 15` unreachable

The `retries >= 2` check in the worker loop sends to `dead_letter` before reaching `BACKOFF_MINUTES[2]`. This means the third backoff (15min) is never used — the effective maximum is 5min.

**Accepted:** For V1, either change to `retries > 2` with `BACKOFF_MINUTES = [0, 5, 15]`, or simply document that dead_letter occurs after 2 retries (0min + 5min).
