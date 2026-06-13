"""
tests/test_circuit_breaker.py

10 unit tests for CircuitBreaker.

Time is controlled via monkeypatching time.monotonic — no real waiting.
No network calls are made.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from orchestrator.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitBreakerState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_cb(threshold: int = 3, timeout: float = 60.0) -> CircuitBreaker:
    """Return a fresh CircuitBreaker (not from the registry)."""
    return CircuitBreaker("test_provider", failure_threshold=threshold, recovery_timeout=timeout)


def exhaust_to_open(cb: CircuitBreaker, threshold: int = 3) -> None:
    """Fire exactly threshold failing calls to open the CB."""
    for _ in range(threshold):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("boom")))  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test 1 — CLOSED → OPEN on threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_closed_to_open_on_threshold():
    """After exactly failure_threshold consecutive failures the CB opens."""
    cb = make_cb(threshold=3)

    assert cb.state is CircuitBreakerState.CLOSED

    for i in range(3):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))  # type: ignore[misc]
        if i < 2:
            assert cb.state is CircuitBreakerState.CLOSED, f"should still be CLOSED after {i + 1}"

    assert cb.state is CircuitBreakerState.OPEN


# ---------------------------------------------------------------------------
# Test 2 — OPEN rejects immediately without calling fn()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_open_rejects_immediately(monkeypatch):
    """While OPEN and timeout not elapsed, fn() is never called."""
    cb = make_cb(threshold=2, timeout=60.0)

    # Force CB to OPEN
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("x")))  # type: ignore[misc]

    assert cb.state is CircuitBreakerState.OPEN

    fn = MagicMock()
    with pytest.raises(CircuitBreakerOpenError):
        cb.call(fn)

    fn.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3 — OPEN → HALF_OPEN after recovery_timeout
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_open_to_half_open_after_timeout(monkeypatch):
    """After recovery_timeout elapses, fn() is called (HALF_OPEN probe)."""
    cb = make_cb(threshold=2, timeout=60.0)

    base_time = 1000.0
    current_time = [base_time]

    def fake_monotonic():
        return current_time[0]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    # Open the CB
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("x")))  # type: ignore[misc]

    assert cb.state is CircuitBreakerState.OPEN

    # Advance time past recovery_timeout
    current_time[0] = base_time + 61.0

    fn = MagicMock(return_value="ok")
    result = cb.call(fn)

    fn.assert_called_once()
    assert result == "ok"


# ---------------------------------------------------------------------------
# Test 4 — HALF_OPEN success → CLOSED
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_half_open_success_closes(monkeypatch):
    """A successful probe in HALF_OPEN transitions to CLOSED and resets counter."""
    cb = make_cb(threshold=2, timeout=60.0)

    base_time = 1000.0
    current_time = [base_time]
    monkeypatch.setattr(time, "monotonic", lambda: current_time[0])

    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("x")))  # type: ignore[misc]

    current_time[0] = base_time + 61.0

    cb.call(lambda: "success")

    assert cb.state is CircuitBreakerState.CLOSED
    assert cb._consecutive_failures == 0
    assert cb._half_open_in_flight is False


# ---------------------------------------------------------------------------
# Test 5 — HALF_OPEN failure → OPEN, original exception propagated
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_half_open_failure_reopens(monkeypatch):
    """
    A failing probe in HALF_OPEN:
      - transitions state back to OPEN
      - propagates the *original* exception (not CircuitBreakerOpenError)
      - resets _half_open_in_flight to False
    """
    cb = make_cb(threshold=2, timeout=60.0)

    base_time = 1000.0
    current_time = [base_time]
    monkeypatch.setattr(time, "monotonic", lambda: current_time[0])

    # Open the CB
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("api_error")))  # type: ignore[misc]

    current_time[0] = base_time + 61.0

    # Probe fails
    with pytest.raises(ValueError, match="api_error"):
        cb.call(lambda: (_ for _ in ()).throw(ValueError("api_error")))  # type: ignore[misc]

    assert cb.state is CircuitBreakerState.OPEN
    assert cb._half_open_in_flight is False


# ---------------------------------------------------------------------------
# Test 6 — Failures below threshold keeps CB CLOSED
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_failure_below_threshold_stays_closed():
    """Exactly threshold-1 failures must NOT open the CB."""
    threshold = 4
    cb = make_cb(threshold=threshold)

    for _ in range(threshold - 1):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("x")))  # type: ignore[misc]

    assert cb.state is CircuitBreakerState.CLOSED


# ---------------------------------------------------------------------------
# Test 7 — Counter resets on success (fail-succeed-fail chain)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_counter_resets_on_success():
    """
    Pattern: fail, success, fail, success, fail.
    Counter resets on each success so CB never reaches threshold=3.
    """
    cb = make_cb(threshold=3)

    def fail():
        raise ValueError("x")

    for _ in range(5):
        try:
            # Alternate fail / success
            cb.call(fail)
        except ValueError:
            pass
        try:
            cb.call(lambda: "ok")
        except Exception:
            pass

    assert cb.state is CircuitBreakerState.CLOSED
    assert cb._consecutive_failures == 0


# ---------------------------------------------------------------------------
# Test 8 — HALF_OPEN rejects additional concurrent calls
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_half_open_rejects_additional_calls(monkeypatch):
    """
    While a probe call is in-flight (in_flight=True), a second concurrent
    call must raise CircuitBreakerOpenError without executing fn().
    """
    cb = make_cb(threshold=2, timeout=60.0)

    base_time = 1000.0
    current_time = [base_time]
    monkeypatch.setattr(time, "monotonic", lambda: current_time[0])

    # Open the CB
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("x")))  # type: ignore[misc]

    current_time[0] = base_time + 61.0

    # Manually transition to HALF_OPEN and set in_flight=True
    cb._state = CircuitBreakerState.HALF_OPEN
    cb._half_open_in_flight = True

    second_fn = MagicMock()
    with pytest.raises(CircuitBreakerOpenError):
        cb.call(second_fn)

    second_fn.assert_not_called()


# ---------------------------------------------------------------------------
# Test 9 — CircuitBreakerOpenError is not self-counted
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cb_open_error_not_self_counted(monkeypatch):
    """
    When CB is OPEN and rejects a call with CircuitBreakerOpenError,
    that rejection must NOT increment _consecutive_failures.
    """
    cb = make_cb(threshold=2, timeout=60.0)

    base_time = 1000.0
    current_time = [base_time]
    monkeypatch.setattr(time, "monotonic", lambda: current_time[0])

    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("x")))  # type: ignore[misc]

    failures_before = cb._consecutive_failures

    # CB is OPEN — this call should be fast-rejected
    with pytest.raises(CircuitBreakerOpenError):
        cb.call(lambda: "ignored")

    assert cb._consecutive_failures == failures_before


# ---------------------------------------------------------------------------
# Test 10 — Stale _half_open_in_flight bug regression
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_half_open_inflight_reset_on_failure(monkeypatch):
    """
    Full chain: HALF_OPEN → probe fails → OPEN → advance monotonic →
    CB re-enters HALF_OPEN → new probe call is ACCEPTED (not blocked).

    This is a regression test for the bug where _half_open_in_flight was
    left True after HALF_OPEN→OPEN transition, permanently blocking recovery.
    """
    cb = make_cb(threshold=2, timeout=60.0)

    base_time = 1000.0
    current_time = [base_time]
    monkeypatch.setattr(time, "monotonic", lambda: current_time[0])

    # Step 1 — Open the CB
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("x")))  # type: ignore[misc]

    assert cb.state is CircuitBreakerState.OPEN

    # Step 2 — Advance time so CB can enter HALF_OPEN
    current_time[0] = base_time + 61.0

    # Step 3 — Probe fails → CB goes back to OPEN, in_flight must be cleared
    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError("probe_fail")))  # type: ignore[misc]

    assert cb.state is CircuitBreakerState.OPEN
    assert cb._half_open_in_flight is False, "BUG: in_flight not reset after HALF_OPEN→OPEN"

    # Step 4 — Advance time again past the new last_failure_time
    current_time[0] = base_time + 200.0  # well past any recovery_timeout

    # Step 5 — New probe call must be accepted (fn is actually called)
    probe_fn = MagicMock(return_value="recovered")
    result = cb.call(probe_fn)

    probe_fn.assert_called_once()
    assert result == "recovered"
    assert cb.state is CircuitBreakerState.CLOSED
