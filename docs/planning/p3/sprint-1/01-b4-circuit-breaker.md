# B4 — Externalized Circuit Breaker State

## Goal

Make Circuit Breaker state survive worker restarts and be shared across all workers. A Gemini outage must open the CB globally (via shared SQLite) and exponential backoff must prevent thundering herd on recovery.

---

## Current State

### `src/orchestrator/circuit_breaker.py:52-84` — In-process singleton per provider

```python
class CircuitBreaker:
    def __init__(
        self,
        provider_name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout < 0:
            raise ValueError("recovery_timeout must be >= 0")
        self._provider_name = provider_name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._consecutive_failures: int = 0
        self._last_failure_time: float = 0.0
        self._state: CircuitBreakerState = CircuitBreakerState.CLOSED
        self._half_open_in_flight: bool = False
```

All state is in-process memory. Each Docker worker creates its own CB instances at import time in `CLOSED` state.

### `src/orchestrator/circuit_breaker.py:222-241` — Process-level registry

```python
_registry: dict[str, CircuitBreaker] = {}

def circuit_breaker_for(provider_name: str, *, failure_threshold: int = 3, recovery_timeout: float = 60.0) -> CircuitBreaker:
    if provider_name not in _registry:
        _registry[provider_name] = CircuitBreaker(provider_name, failure_threshold, recovery_timeout)
    return _registry[provider_name]
```

Singleton per process, not shared across processes/containers.

### `src/orchestrator/agents/executor/providers.py:1-40` — Provider chain

```python
MODEL_GEMINI = "gemini-2.5-flash"
MODEL_GROQ = "llama-3.3-70b-versatile"
MODEL_CLAUDE = "claude-sonnet-4-6"

TIMEOUT_SECONDS = 60
MAX_RETRIES = 1

_PROVIDER_CHAIN: dict[str, list] = {
    "low": [],
    "medium": [],
    "high": [],
}
```

No exponential backoff. Recovery is a flat 60s timeout.

---

## Changes

### 1. Create `src/orchestrator/storage/__init__.py`

```python
"""Storage package. Exports _sqlite_connect used by all storage modules."""

import sqlite3
from pathlib import Path


def _sqlite_connect(db_path: Path) -> sqlite3.Connection:
    """Single connection factory. Always enables row_factory + WAL mode + autocommit.
    See 00-README.md §Canonical Patterns for contract."""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
```

### 2. Create `src/orchestrator/storage/lock.py` with `CircuitBreakerStore` interface

```python
from abc import ABC, abstractmethod
from typing import Callable, Optional
from orchestrator.circuit_breaker import CircuitBreakerState

class CircuitBreakerStore(ABC):
    @abstractmethod
    def get_state(self, provider: str) -> Optional[dict]: ...
    @abstractmethod
    def set_state(self, provider: str, state: dict) -> None: ...
    @abstractmethod
    def atomic_update(self, provider: str, txn: Callable) -> bool: ...


class SqliteCircuitBreakerStore(CircuitBreakerStore):
    def __init__(self, db_dir: Path):
        # Use canonical _sqlite_connect() — never sqlite3.connect() directly.
        # See 00-README.md §Canonical Patterns
        self._conn = _sqlite_connect(db_dir / "coordination.db")
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

    def get_state(self, provider: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM cb_state WHERE provider = ?", (provider,)
        ).fetchone()
        if row:
            return dict(row)
        return None

    def set_state(self, provider: str, state: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cb_state "
            "(provider, state, failures, last_failure_at, recovery_timeout) "
            "VALUES (?, ?, ?, ?, ?)",
            (provider, state["state"], state.get("failures", 0),
             state.get("last_failure_at"), state.get("recovery_timeout"))
        )
        self._conn.commit()

    def atomic_update(self, provider: str, txn: Callable) -> bool:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT * FROM cb_state WHERE provider = ?", (provider,)
            ).fetchone()
            new_state = txn(dict(row) if row else {})
            self._conn.execute(
                "INSERT OR REPLACE INTO cb_state "
                "(provider, state, failures, last_failure_at, recovery_timeout) "
                "VALUES (?, ?, ?, ?, ?)",
                (provider, new_state["state"], new_state.get("failures", 0),
                 new_state.get("last_failure_at"), new_state.get("recovery_timeout"))
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            return False
```

### 3. Refactor `CircuitBreaker` to accept a store

Add parameter to `CircuitBreaker.__init__`:

```python
class CircuitBreaker:
    def __init__(self, provider_name: str, store: CircuitBreakerStore,
                 failure_threshold: int = 3, recovery_timeout: float = 60.0):
        self._provider_name = provider_name
        self._store = store
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._load_state()

    def _load_state(self) -> None:
        state = self._store.get_state(self._provider_name)
        if state:
            self._state = CircuitBreakerState(state["state"])
            self._consecutive_failures = state.get("failures", 0)
            self._last_failure_time = state.get("last_failure_at", 0.0)
        else:
            self._state = CircuitBreakerState.CLOSED
            self._consecutive_failures = 0
            self._last_failure_time = 0.0

    def _persist_state(self) -> None:
        self._store.set_state(self._provider_name, {
            "state": self._state.value,
            "failures": self._consecutive_failures,
            "last_failure_at": self._last_failure_time,
            "recovery_timeout": self._recovery_timeout,
        })
```

Every mutation to `_state`, `_consecutive_failures`, `_last_failure_time` must call `_persist_state()`.

### 4. Add exponential backoff to recovery

```python
RECOVERY_BACKOFF = [60, 120, 240, 480, 900]  # 1min → 15min cap
```

In `_on_failure()`, after transitioning to OPEN:

```python
backoff_index = min(self._consecutive_failures // self._failure_threshold, len(RECOVERY_BACKOFF) - 1)
self._recovery_timeout = RECOVERY_BACKOFF[backoff_index]
```

### 5. Reactive HALF_OPEN probe in provider layer

Add `_call_with_half_open_probe()` to `src/orchestrator/agents/executor/providers.py`:

```python
from orchestrator.circuit_breaker import CircuitBreakerState

class ProbeSlotBusyError(CircuitBreakerOpenError):
    """HALF_OPEN probe slot is held by another worker — yield without burning retry.

    NOTE: defined here for reference; final definition lives in exceptions.py."""

def _call_with_half_open_probe(conn_coord, provider_name, fn, *args):
    """Reactive HALF_OPEN probe: acquire token only when calling the LLM.
    Prevents wasted probes on non-LLM stages (apply, validator if local)."""
    state = conn_coord.execute(
        "SELECT * FROM cb_state WHERE provider = ?", (provider_name,)
    ).fetchone()

    if state and state["state"] == CircuitBreakerState.OPEN.value:
        last_failure = state["last_failure_at"]
        recovery_timeout = state["recovery_timeout"]
        import time
        if time.time() < last_failure + recovery_timeout:
            raise CircuitBreakerOpenError(provider_name)
        # Timeout expired — no external process updates state, so we do it here
        conn_coord.execute("BEGIN IMMEDIATE")
        # Re-check inside lock (another worker may have beaten us)
        fresh = conn_coord.execute(
            "SELECT state FROM cb_state WHERE provider = ?", (provider_name,)
        ).fetchone()
        if fresh and fresh["state"] == CircuitBreakerState.OPEN.value:
            conn_coord.execute(
                f"UPDATE cb_state SET state = '{CircuitBreakerState.HALF_OPEN.value}' WHERE provider = ?", (provider_name,)
            )
        conn_coord.commit()
        # Fall through to probe attempt
        
    current_state = state["state"] if state else CircuitBreakerState.CLOSED.value
    if current_state != CircuitBreakerState.HALF_OPEN.value and not (state and state["state"] == CircuitBreakerState.OPEN.value):
        return fn(*args)  # CLOSED

    # HALF_OPEN: try acquire probe token reactively
    conn_coord.execute("BEGIN IMMEDIATE")
    token = conn_coord.execute(
        "SELECT 1 FROM half_open_probe WHERE provider = ?", (provider_name,)
    ).fetchone()
    if token:
        conn_coord.rollback()
        raise ProbeSlotBusyError(provider_name)  # caught by worker loop → yield

    conn_coord.execute(
        "INSERT INTO half_open_probe (provider, worker_id, acquired_at) "
        "VALUES (?, ?, datetime('now'))",
        (provider_name, os.environ.get("WORKER_ID", "unknown"))
    )
    conn_coord.commit()
    try:
        result = fn(*args)  # actual LLM call
        return result
    except Exception:
        _release_probe_token(conn_coord, provider_name)
        conn_coord.commit()
        raise

def _release_probe_token(conn_coord, provider=None):
    if provider:
        conn_coord.execute("DELETE FROM half_open_probe WHERE provider = ?", (provider,))
    else:
        worker = os.environ.get("WORKER_ID", "unknown")
        conn_coord.execute("DELETE FROM half_open_probe WHERE worker_id = ?", (worker,))

def _cleanup_stale_probes(conn_coord):
    conn_coord.execute(
        "DELETE FROM half_open_probe "
        "WHERE acquired_at < datetime('now', '-5 minutes')"
    )
```

---

## Files to Create/Modify

- **NEW** `src/orchestrator/storage/__init__.py` — Package marker
- **NEW** `src/orchestrator/storage/lock.py` — `CircuitBreakerStore` interface + `SqliteCircuitBreakerStore`
- `src/orchestrator/circuit_breaker.py` — Accept store, persist state on mutation, exponential backoff
- `src/orchestrator/agents/executor/providers.py` — `_call_with_half_open_probe()`, `_release_probe_token()`
- `src/orchestrator/clients/bootstrap.py` — Initialize `coordination.db` (cb_state + half_open_probe tables)

---

## Acceptance Criteria

- [ ] CB state survives worker restart (SQLite persistence)
- [ ] A Gemini outage opens CB globally via shared `coordination.db`
- [ ] Exponential backoff prevents thundering herd on recovery (1min → 15min cap)
- [ ] `ProbeSlotBusyError(CircuitBreakerOpenError)` — does not count toward retry budget
- [ ] Non-LLM stages (apply, file-only validator) never waste the probe slot

---

## Test skeleton (create before running pytest)

Append new cases to the existing `tests/test_circuit_breaker.py`:
```python
def test_cb_state_persists():
    """Verify state transitions are written to SQLite via CircuitBreakerStore."""
    pass

def test_exponential_backoff():
    """Verify recovery timeout increases exponentially on repeated failures."""
    pass

def test_half_open_probe_lock():
    """Verify _call_with_half_open_probe raises ProbeSlotBusy if lock held."""
    pass
```

## Verification

```bash
pytest tests/test_circuit_breaker.py -v
pytest tests/test_circuit_breaker_integration.py -v

# Manual: open CB, verify state persists
python -c "
from orchestrator.storage.lock import SqliteCircuitBreakerStore
from pathlib import Path
store = SqliteCircuitBreakerStore(Path('/tmp'))
store.set_state('gemini', {'state': CircuitBreakerState.OPEN.value, 'failures': 3, 'last_failure_at': 100.0, 'recovery_timeout': 60.0})
state = store.get_state('gemini')
assert state['state'] == CircuitBreakerState.OPEN.value
print('CB state persisted')
"
```

## Rollback

```bash
git rm src/orchestrator/storage/__init__.py src/orchestrator/storage/lock.py
git checkout -- src/orchestrator/circuit_breaker.py
git checkout -- src/orchestrator/agents/executor/providers.py
```
