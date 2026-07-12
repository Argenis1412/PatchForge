"""
circuit_breaker.py

Provides a per-provider CircuitBreaker that isolates LLM provider failures.

State machine:
    CLOSED   → (failure_threshold consecutive failures)       → OPEN
    OPEN     → (recovery_timeout elapsed since last failure)  → HALF_OPEN
    HALF_OPEN → (successful call)                             → CLOSED
    HALF_OPEN → (failed call)                                 → OPEN (propagates original exc)

# Threshold semantics: failure_threshold counts *calls*, not tasks.
# In Executor with MAX_RETRIES=1, each failing task produces 2 calls to the CB.
# Effective task threshold to OPEN ≈ failure_threshold / 2.
# Example: failure_threshold=3 → ~1.5 failing tasks trigger OPEN.

# B4: Uses time.time() for last_failure_at — NOT time.monotonic().
# This ensures cross-process comparability: monotonic resets on reboot,
# which would break the retry-after calculation when state persists in
# SQLite across worker restarts.
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Callable, TypeVar

from orchestrator.exceptions import CircuitBreakerOpenError  # re-exported for callers
from orchestrator.observability.events import FailureType, log_event, log_failure
from orchestrator.storage.lock import CircuitBreakerStore, _InMemoryCircuitBreakerStore

T = TypeVar("T")


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class CircuitBreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# CircuitBreakerOpenError is defined in orchestrator.exceptions (canonical location).
# It is re-exported here for convenient import by callers who only import
# from orchestrator.circuit_breaker.
# See exceptions.py for the class definition and rationale.

# Exponential backoff schedule for recovery_timeout (seconds).
# Index = (consecutive_failures - 1) // failure_threshold (capped at last element).
RECOVERY_BACKOFF: list[float] = [60.0, 120.0, 240.0, 480.0, 900.0]


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """
    Single-provider circuit breaker.

    Thread safety: a single ``threading.Lock`` (``self._lock``) serializes
    all mutations of instance state (``_state``, ``_consecutive_failures``,
    ``_last_failure_time``, ``_recovery_timeout``, ``_half_open_in_flight``)
    and their persistence via ``_persist_state()``. The caller-supplied
    ``fn()`` is ALWAYS executed with the lock released — network calls must
    not block other threads' state transitions.

    Lock ordering (must always be acquired in this order to prevent deadlock):
        _init_lock → _registry_lock → CB._lock → store._conn_lock

    Documented limitations:

    1. ``SqliteCircuitBreakerStore`` thread-affinity: RESOLVED (issue #219).
       The store now passes ``check_same_thread=False`` to ``_sqlite_connect()``
       and serializes all connection access with ``store._conn_lock``.
    2. ``fn()``-masking window: because ``fn()`` runs unlocked, a
       concurrent thread's outcome handler (``_on_success``/``_on_failure``)
       can transition state between one thread's ``fn()`` returning and
       that thread acquiring the lock in its own outcome handler. One
       outcome can therefore mask another (e.g. a success can reset
       counters just incremented by a concurrent failure). This lock
       provides atomicity of individual transitions, not linearizability
       of concurrent outcomes. Accepted by design.
    3. ``circuit_breaker_for()`` registry race: RESOLVED (issue #219).
       The module-level ``_registry`` check-then-set is now guarded by
       ``_registry_lock``, ensuring a single instance per provider name.

    Internal invariant: _half_open_in_flight is ALWAYS False when
    _state is CLOSED or OPEN. It is only True transiently when
    _state is HALF_OPEN and a probe call is executing.

    B4: State is persisted to store on every mutation. SQLite store enables
    cross-worker sharing; in-process fallback used when no store is provided.
    """

    def __init__(
        self,
        provider_name: str,
        store: CircuitBreakerStore,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout < 0:
            raise ValueError("recovery_timeout must be >= 0")
        self._provider_name = provider_name
        self._store = store
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout

        # Internal state — loaded from store on init, authoritative in store.
        self._consecutive_failures: int = 0
        self._last_failure_time: float = 0.0
        self._state: CircuitBreakerState = CircuitBreakerState.CLOSED
        # Process-level guard against concurrent probes in the same worker.
        self._half_open_in_flight: bool = False

        # Serializes state mutations. Lock (not RLock): the call graph is
        # a tree (call → _execute_* → _on_* → _persist_state) with no
        # re-entrancy — _reload_state calls store.get_state which is a
        # plain SQLite read / dict lookup, never re-enters call().
        # Must NEVER be held while fn() runs (fn is a network call).
        # See class docstring for documented limitations this lock does
        # not close (SQLite thread-affinity, fn()-masking, registry race).
        self._lock = threading.Lock()

        self._load_state()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Load state from store at init. Called once during __init__."""
        row = self._store.get_state(self._provider_name)
        if row:
            self._state = CircuitBreakerState(row["state"])
            self._consecutive_failures = row.get("failures") or 0
            self._last_failure_time = row.get("last_failure_at") or 0.0
            if row.get("recovery_timeout") is not None:
                self._recovery_timeout = float(row["recovery_timeout"])
        else:
            self._state = CircuitBreakerState.CLOSED
            self._consecutive_failures = 0
            self._last_failure_time = 0.0

    def _persist_state(self) -> None:
        self._store.set_state(
            self._provider_name,
            {
                "state": self._state.value,
                "failures": self._consecutive_failures,
                "last_failure_at": self._last_failure_time,
                "recovery_timeout": self._recovery_timeout,
            },
        )

    def _reload_state(self) -> None:
        """Sync in-memory state from store. Picks up changes written by other workers.
        Unlike _load_state(), keeps current defaults when no DB row exists yet."""
        row = self._store.get_state(self._provider_name)
        if row:
            self._state = CircuitBreakerState(row["state"])
            self._consecutive_failures = row.get("failures") or 0
            self._last_failure_time = row.get("last_failure_at") or 0.0
            if row.get("recovery_timeout") is not None:
                self._recovery_timeout = float(row["recovery_timeout"])

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitBreakerState:
        """Read-only view of current state."""
        return self._state

    def call(self, fn: Callable[[], T]) -> T:
        """Execute fn() through the circuit breaker.

        Returns:
            Whatever fn() returns on success.
        Raises:
            CircuitBreakerOpenError: if the CB is OPEN and timeout has not
                elapsed, or if a HALF_OPEN probe is already in flight.
            Exception: whatever fn() raises — propagated as-is (never wrapped).
        """
        go_closed = False
        go_half_open = False
        retry_after_reject: float | None = None

        with self._lock:
            self._reload_state()
            now = time.time()

            if self._state == CircuitBreakerState.CLOSED:
                go_closed = True
            elif self._state == CircuitBreakerState.OPEN:
                retry_after = self._last_failure_time + self._recovery_timeout
                if now < retry_after:
                    retry_after_reject = retry_after
                else:
                    # Timeout elapsed — transition to HALF_OPEN and probe.
                    self._state = CircuitBreakerState.HALF_OPEN
                    self._persist_state()
                    go_half_open = True
            else:
                # HALF_OPEN
                go_half_open = True

        # Lock released — never hold the lock across fn().
        if retry_after_reject is not None:
            raise CircuitBreakerOpenError(
                provider=self._provider_name,
                state=CircuitBreakerState.OPEN,
                retry_after=retry_after_reject,
            )
        if go_closed:
            return self._execute_closed(fn)
        if go_half_open:
            return self._execute_half_open(fn)
        # Unreachable — all three branches set exactly one flag.
        raise AssertionError("CircuitBreaker.call: no dispatch flag set")

    def reset(self) -> None:
        """Force the CB back to CLOSED. Intended for tests only."""
        with self._lock:
            self._state = CircuitBreakerState.CLOSED
            self._consecutive_failures = 0
            self._last_failure_time = 0.0
            self._half_open_in_flight = False
            self._persist_state()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _execute_closed(self, fn: Callable[[], T]) -> T:
        try:
            result = fn()
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)  # always re-raises

    def _execute_half_open(self, fn: Callable[[], T]) -> T:
        with self._lock:
            if self._half_open_in_flight:
                # A probe is already running — reject this call.
                # Note: this flag is process-local. Cross-worker HALF_OPEN
                # contention is not prevented — multiple workers may probe
                # simultaneously. This is an accepted relaxation: the first
                # successful probe resets to CLOSED, and others see the
                # updated state on their next _reload_state().
                retry_after_reject = self._last_failure_time + self._recovery_timeout
                current_state = self._state
                probe_taken = False
            else:
                self._half_open_in_flight = True
                probe_taken = True
                retry_after_reject = 0.0
                current_state = self._state

        if not probe_taken:
            raise CircuitBreakerOpenError(
                provider=self._provider_name,
                state=current_state,
                retry_after=retry_after_reject,
                message="probe already in flight",
            )
        _outcome_handled = False
        try:
            result = fn()
            _outcome_handled = True
            self._on_success()
            return result
        except Exception as exc:
            _outcome_handled = True
            self._on_failure(exc)  # always re-raises
        finally:
            # BaseException (KeyboardInterrupt, SystemExit, …) bypasses the
            # except-Exception clause above — clear the flag so future probes
            # are not permanently blocked after a non-Exception escape from fn().
            if not _outcome_handled:
                with self._lock:
                    self._half_open_in_flight = False

    def _on_failure(self, exc: Exception) -> None:
        emit_log_msg: str | None = None
        failures_snapshot = 0
        with self._lock:
            self._consecutive_failures += 1
            # CRITICAL: Always reset in-flight flag before state transition.
            # If CB was in HALF_OPEN and the probe call failed, this flag
            # must be False before moving to OPEN. Otherwise, after
            # recovery_timeout elapses and CB re-enters HALF_OPEN, the stale
            # flag permanently blocks recovery.
            self._half_open_in_flight = False
            if self._consecutive_failures >= self._failure_threshold:
                previous_state = self._state
                self._state = CircuitBreakerState.OPEN
                self._last_failure_time = time.time()
                # Exponential backoff: index = (consecutive-1) // threshold, capped.
                backoff_index = min(
                    (self._consecutive_failures - 1) // self._failure_threshold,
                    len(RECOVERY_BACKOFF) - 1,
                )
                self._recovery_timeout = RECOVERY_BACKOFF[backoff_index]
                # Capture failures snapshot inside the lock so the log
                # message and data.failures cannot drift under concurrency.
                failures_snapshot = self._consecutive_failures
                # Differentiate message: first opening vs re-opening after a probe.
                if previous_state == CircuitBreakerState.HALF_OPEN:
                    emit_log_msg = (
                        f"CircuitBreaker for '{self._provider_name}' re-opened after "
                        f"probe failure (was HALF_OPEN)."
                    )
                else:
                    emit_log_msg = (
                        f"CircuitBreaker for '{self._provider_name}' opened after "
                        f"{failures_snapshot} consecutive failures."
                    )
            self._persist_state()

        if emit_log_msg is not None:
            # Auto-observability: emit only on transition to OPEN. Outside
            # the lock — log I/O must not block other threads' transitions.
            log_failure(
                trace_id="circuit_breaker",
                run_id=self._provider_name,
                stage="circuit_breaker",
                error_type=FailureType.CIRCUIT_BREAKER_OPEN,
                message=emit_log_msg,
                source="circuit_breaker",
                data={"provider": self._provider_name, "failures": failures_snapshot},
            )
        raise exc  # always re-raise the original exception

    def _on_success(self) -> None:
        with self._lock:
            # Capture inside the lock: a stale pre-lock read could otherwise
            # let a concurrent transition suppress or spuriously emit the
            # circuit_recovered event.
            previous_state = self._state
            self._consecutive_failures = 0
            self._half_open_in_flight = False  # safety: clear if was HALF_OPEN
            self._state = CircuitBreakerState.CLOSED
            self._persist_state()
        if previous_state != CircuitBreakerState.CLOSED:
            # Auto-observability: emit only on transition to CLOSED from another state.
            log_event(
                trace_id="circuit_breaker",
                run_id=self._provider_name,
                level="info",
                source="circuit_breaker",
                stage="circuit_breaker",
                event="circuit_recovered",
                data={
                    "provider": self._provider_name,
                    "previous_state": previous_state.value,
                },
            )


# ---------------------------------------------------------------------------
# Module-level registry (singleton per provider)
# ---------------------------------------------------------------------------

_registry: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def circuit_breaker_for(
    provider_name: str,
    *,
    store: CircuitBreakerStore | None = None,
    failure_threshold: int = 3,
    recovery_timeout: float = 60.0,
) -> CircuitBreaker:
    """Return the shared CircuitBreaker for a provider.

    State is shared across all callers within the process.
    The first call creates the instance; subsequent calls with the same
    provider_name return the same object (singleton per process).

    When store is None an in-process _InMemoryCircuitBreakerStore is used
    (backwards-compatible behaviour for callers that predate B4).
    """
    with _registry_lock:
        if provider_name not in _registry:
            _registry[provider_name] = CircuitBreaker(
                provider_name,
                store if store is not None else _InMemoryCircuitBreakerStore(),
                failure_threshold,
                recovery_timeout,
            )
        return _registry[provider_name]
