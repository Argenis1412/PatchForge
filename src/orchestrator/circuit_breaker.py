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
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Callable, TypeVar

from orchestrator.exceptions import CircuitBreakerOpenError, PatchForgeError  # noqa: F401
from orchestrator.observability.events import FailureType, log_event, log_failure

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


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """
    Single-provider circuit breaker.

    Thread safety: no locking is used (consistent with project convention
    of no-threading + GIL protection in CPython). Check-then-set without
    lock is acceptable here.

    Internal invariant: _half_open_in_flight is ALWAYS False when
    _state is CLOSED or OPEN. It is only True transiently when
    _state is HALF_OPEN and a probe call is executing.
    """

    def __init__(
        self,
        provider_name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        self._provider_name = provider_name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout

        # Internal state
        self._consecutive_failures: int = 0
        self._last_failure_time: float = 0.0
        self._state: CircuitBreakerState = CircuitBreakerState.CLOSED
        # Mutual exclusion flag for HALF_OPEN: only one probe call in flight.
        self._half_open_in_flight: bool = False

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
        now = time.monotonic()

        if self._state == CircuitBreakerState.CLOSED:
            return self._execute_closed(fn)

        if self._state == CircuitBreakerState.OPEN:
            retry_after = self._last_failure_time + self._recovery_timeout
            if now < retry_after:
                # Still within cooldown — fast-reject.
                raise CircuitBreakerOpenError(
                    provider=self._provider_name,
                    state=self._state,
                    retry_after=retry_after,
                )
            # Timeout elapsed — transition to HALF_OPEN and probe.
            self._state = CircuitBreakerState.HALF_OPEN
            return self._execute_half_open(fn)

        # _state == HALF_OPEN
        return self._execute_half_open(fn)

    def reset(self) -> None:
        """Force the CB back to CLOSED. Intended for tests only."""
        self._state = CircuitBreakerState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time = 0.0
        self._half_open_in_flight = False

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
        if self._half_open_in_flight:
            # A probe is already running — reject this call.
            raise CircuitBreakerOpenError(
                provider=self._provider_name,
                state=self._state,
                retry_after=self._last_failure_time + self._recovery_timeout,
                message="probe already in flight",
            )
        self._half_open_in_flight = True
        try:
            result = fn()
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)  # always re-raises

    def _on_failure(self, exc: Exception) -> None:
        self._consecutive_failures += 1
        # CRITICAL: Always reset in-flight flag before state transition.
        # If CB was in HALF_OPEN and the probe call failed, this flag
        # must be False before moving to OPEN. Otherwise, after
        # recovery_timeout elapses and CB re-enters HALF_OPEN, the stale
        # flag permanently blocks recovery.
        self._half_open_in_flight = False
        if self._consecutive_failures >= self._failure_threshold:
            self._state = CircuitBreakerState.OPEN
            self._last_failure_time = time.monotonic()
            # Auto-observability: emit only on transition to OPEN.
            log_failure(
                trace_id="circuit_breaker",
                run_id=self._provider_name,
                stage="circuit_breaker",
                error_type=FailureType.CIRCUIT_BREAKER_OPEN,
                message=(
                    f"CircuitBreaker for '{self._provider_name}' opened after "
                    f"{self._consecutive_failures} consecutive failures."
                ),
                source="circuit_breaker",
                data={"provider": self._provider_name, "failures": self._consecutive_failures},
            )
        raise exc  # always re-raise the original exception

    def _on_success(self) -> None:
        previous_state = self._state
        self._consecutive_failures = 0
        self._half_open_in_flight = False  # safety: clear if was HALF_OPEN
        self._state = CircuitBreakerState.CLOSED
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


def circuit_breaker_for(
    provider_name: str,
    *,
    failure_threshold: int = 3,
    recovery_timeout: float = 60.0,
) -> CircuitBreaker:
    """Return the shared CircuitBreaker for a provider.

    State is shared across all callers within the process.
    The first call creates the instance; subsequent calls with the same
    provider_name return the same object (singleton per process).
    """
    if provider_name not in _registry:
        _registry[provider_name] = CircuitBreaker(
            provider_name, failure_threshold, recovery_timeout
        )
    return _registry[provider_name]
