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

## §2 — Fix for B4 (Circuit Breaker)

### 2.1 `_call_with_half_open_probe` must be the SOLE gatekeeper

**Conscious decision:** `_call_with_half_open_probe()` replaces `CircuitBreaker.call()` as the production entry point. The old `CircuitBreaker` class becomes a legacy wrapper (kept for existing tests). **Reason:** The old one uses in-process state with `time.monotonic()` and `_half_open_in_flight` that cannot work across workers. The new one operates exclusively on shared SQLite.

**Changes in `src/orchestrator/circuit_breaker.py`:**
- The current `__init__` receives `(provider_name, failure_threshold, recovery_timeout)` — add `store: CircuitBreakerStore` parameter
- Add `_load_state()` and `_persist_state()` (defined in b4 section 3, but with the `.value` fix below)
- `_on_failure()` instead of mutating `self._state` directly, call `_persist_state()` after every transition
- **DO NOT** modify `CircuitBreaker.call()` — leave as dead code (only for existing unit tests that don't touch SQLite)

### 2.2 Fix: `CircuitBreakerOpenError(provider_name)` requires 3 arguments

In `_call_with_half_open_probe()` (b4 section 5, line 240), **replace**:

```python
# WRONG (TypeError):
raise CircuitBreakerOpenError(provider_name)
raise ProbeSlotBusyError(provider_name)   # same
```

**With:**

```python
# CORRECT:
raise CircuitBreakerOpenError(
    provider=provider_name,
    state=CircuitBreakerState.OPEN,
    retry_after=last_failure + recovery_timeout,
)
```

And for `ProbeSlotBusyError` (line 265):

```python
# WRONG:
raise ProbeSlotBusyError(provider_name)

# CORRECT:
raise ProbeSlotBusyError(
    provider=provider_name,
    state=CircuitBreakerState.HALF_OPEN,
    retry_after=time.time() + 300,  # probe timeout ~5min
    message="half-open probe slot occupied by another worker",
)
```

### 2.3 `ProbeSlotBusyError` must exist before B4

Add to `src/orchestrator/exceptions.py` **before or during B4** (don't wait for post-audit):

```python
class ProbeSlotBusyError(CircuitBreakerOpenError):
    """Raised when all half-open probe slots are occupied.
    Caller should yield and retry without burning a retry count."""
```

### 2.4 Fix: store in `storage/lock.py`, NOT in `storage/circuit_breaker_store.py`

**Important:** B4 section 2 creates `CircuitBreakerStore` and `SqliteCircuitBreakerStore` in `src/orchestrator/storage/lock.py`. But `04-post-audit-fixes.md` references `src/orchestrator/storage/circuit_breaker_store.py` (which is never created). When implementing B4, ignore any reference to `circuit_breaker_store.py` — the real file is `lock.py`.

### 2.5 `_call_with_half_open_probe` must handle state → HALF_OPEN (OPEN timeout expired)

In b4 section 5, the block that checks OPEN timeout currently does:

```python
if time.time() < last_failure + recovery_timeout:
    raise CircuitBreakerOpenError(provider_name)
# Timeout expired — no external process updates state, so we do it here
conn_coord.execute("BEGIN IMMEDIATE")
```

**This is fine** but is missing: after updating to HALF_OPEN and committing, it must reset `last_failure_at` to 0 (so the next `_call_with_half_open_probe` does not see OPEN again). Add:

```python
# After UPDATE cb_state SET state = 'half_open':
conn_coord.execute(
    "UPDATE cb_state SET last_failure_at = NULL WHERE provider = ?",
    (provider_name,)
)
```

### 2.6 Fix: persistent `consecutive_failures` counter in the store

b4 section 5 `_call_with_half_open_probe` does not update `failures` (failure count) in the CB store when a probe error occurs. This means exponential backoff (using `consecutive_failures // failure_threshold`) never progresses. Add to the `except Exception` block of the probe:

```python
except Exception:
    _release_probe_token(conn_coord, provider_name)
    conn_coord.execute(
        "UPDATE cb_state SET failures = failures + 1 WHERE provider = ?",
        (provider_name,)
    )
    conn_coord.execute(
        "UPDATE cb_state SET state = ?, last_failure_at = ? WHERE provider = ?",
        (CircuitBreakerState.OPEN.value, time.time(), provider_name)
    )
    conn_coord.commit()
    raise
```

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

### 7.1 Two CB mechanisms not reconciled (architectural)

`CircuitBreaker.call()` (old, in-process) and `_call_with_half_open_probe()` (new, SQLite) coexist. **Decision:** `_call_with_half_open_probe` is the sole production gatekeeper. The old `CircuitBreaker.call()` is kept exclusively for existing unit tests (`test_circuit_breaker.py`) that don't touch SQLite.

**Impact on test coverage:** `test_circuit_breaker.py` tests test the legacy mechanism, not the new one. New integration tests against SQLite are needed to validate cross-worker behavior. This is in the B4 test skeleton.

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
