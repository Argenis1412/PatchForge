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

### 5. Wire SqliteCircuitBreakerStore into providers.py

After audit, the reactive HALF_OPEN probe function and its helpers (`_call_with_half_open_probe`, `ProbeSlotBusyError`, `_release_probe_token`, `_cleanup_stale_probes`) were **removed** in favor of a simpler approach: inject `SqliteCircuitBreakerStore` into the existing `circuit_breaker_for()` calls so `CircuitBreaker.call()` reads/writes state directly to shared SQLite.

**Actual implementation** (see commit `e81fafe` / `ac978c7`):

```python
# providers.py — module-level store, shared by all 3 CBs
from pathlib import Path
from orchestrator.storage.lock import SqliteCircuitBreakerStore

_coord_db_dir = Path(os.getenv("PATCHFORGE_DATA_DIR", "."))
_coord_store = SqliteCircuitBreakerStore(_coord_db_dir)

_cb_gemini = circuit_breaker_for("gemini", store=_coord_store)
_cb_groq   = circuit_breaker_for("groq", store=_coord_store)
_cb_claude = circuit_breaker_for("claude", store=_coord_store)
```

**Key design decisions:**
- `CircuitBreaker.call()` calls `_reload_state()` at the start — picks up OPEN/HALF_OPEN state written by any worker
- `_reload_state()` reads from the shared SQLite store — no caching, always fresh
- `time.time()` replaces `time.monotonic()` for restart-safe persistence across workers
- `_half_open_in_flight` is process-local only — cross-worker HALF_OPEN contention is NOT prevented (accepted relaxation: first successful probe resets to CLOSED for all)
- The `half_open_probe` table exists in `coordination.db` but is **unused** by any production code
- `_on_failure()` uses exponential backoff: `RECOVERY_BACKOFF = [60, 120, 240, 480, 900]`

---

## Files to Create/Modify

- **NEW** `src/orchestrator/storage/lock.py` — `CircuitBreakerStore` interface + `SqliteCircuitBreakerStore` + `_InMemoryCircuitBreakerStore`
- `src/orchestrator/storage/__init__.py` — Add `_sqlite_connect()` canonical factory
- `src/orchestrator/circuit_breaker.py` — Accept store, `_load_state()`/`_persist_state()`, `_reload_state()`, exponential backoff, `time.time()`
- `src/orchestrator/agents/executor/providers.py` — Inject `SqliteCircuitBreakerStore` into `circuit_breaker_for()`

---

## Acceptance Criteria

- [ ] CB state survives worker restart — `SqliteCircuitBreakerStore` persists to `coordination.db`
- [ ] A Gemini outage opens CB globally — `_reload_state()` on each `call()` reads latest state from shared SQLite
- [ ] Exponential backoff prevents thundering herd on recovery (60s → 900s cap)
- [ ] Cross-worker HALF_OPEN contention is NOT prevented — accepted relaxation (first probe success resets CB for all)

---

## Test skeleton (appended to `tests/test_circuit_breaker.py`)

```python
def test_cb_state_persists():
    """Verify state transitions are written to SQLite via CircuitBreakerStore."""

def test_exponential_backoff():
    """Verify recovery timeout increases exponentially on repeated failures."""

def test_cross_worker_state_sharing():
    """Verify _reload_state() in call() picks up OPEN state written by another CB instance."""
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
