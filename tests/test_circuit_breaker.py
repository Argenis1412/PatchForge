"""
tests/test_circuit_breaker.py

10 unit tests for CircuitBreaker + 3 B4 tests for SQLite-backed store.

Time is controlled via monkeypatching time.time — no real waiting.
No network calls are made.
"""

from __future__ import annotations

import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from orchestrator.circuit_breaker import (
    RECOVERY_BACKOFF,
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitBreakerState,
)
from orchestrator.storage.lock import CircuitBreakerStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_cb(
    threshold: int = 3,
    timeout: float = 60.0,
    store: CircuitBreakerStore | None = None,
) -> CircuitBreaker:
    """Return a fresh CircuitBreaker (not from the registry)."""
    if store is None:
        store = MagicMock(spec=CircuitBreakerStore)
        store.get_state.return_value = None
        store.set_state.return_value = None
    return CircuitBreaker(
        "test_provider", store, failure_threshold=threshold, recovery_timeout=timeout
    )


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

    def fake_time():
        return current_time[0]

    monkeypatch.setattr(time, "time", fake_time)

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
    monkeypatch.setattr(time, "time", lambda: current_time[0])

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
    monkeypatch.setattr(time, "time", lambda: current_time[0])

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
        with contextlib.suppress(ValueError):
            cb.call(fail)
        with contextlib.suppress(Exception):
            cb.call(lambda: "ok")

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
    monkeypatch.setattr(time, "time", lambda: current_time[0])

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
    monkeypatch.setattr(time, "time", lambda: current_time[0])

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
    monkeypatch.setattr(time, "time", lambda: current_time[0])

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


# ---------------------------------------------------------------------------
# B4 Test 11 — SQLite state persistence across "restarts"
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cb_state_persists(tmp_path):
    """State transitions written to SQLite; new instance reads same state (restart sim)."""
    from orchestrator.storage.lock import SqliteCircuitBreakerStore

    store1 = SqliteCircuitBreakerStore(tmp_path)
    cb1 = make_cb(threshold=3, store=store1)

    # Open CB with 3 failures
    for _ in range(3):
        with pytest.raises(ValueError):
            cb1.call(lambda: (_ for _ in ()).throw(ValueError("x")))  # type: ignore[misc]

    assert cb1.state is CircuitBreakerState.OPEN

    # "Restart": new store + new CB instance, same DB directory
    store2 = SqliteCircuitBreakerStore(tmp_path)
    cb2 = CircuitBreaker("test_provider", store2, failure_threshold=3)

    assert cb2.state is CircuitBreakerState.OPEN
    assert cb2._consecutive_failures == 3


# ---------------------------------------------------------------------------
# B4 Test 12 — Exponential backoff schedule
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_exponential_backoff(monkeypatch):
    """Recovery timeout increases exponentially as consecutive failures accumulate."""
    store = MagicMock(spec=CircuitBreakerStore)
    store.get_state.return_value = None
    store.set_state.return_value = None

    cb = make_cb(threshold=3, store=store)

    current_time = [0.0]
    monkeypatch.setattr(time, "time", lambda: current_time[0])

    def fail():
        raise ValueError("x")

    # failures 1-3 → 60s  (first OPEN at consecutive=3)
    for _ in range(3):
        with pytest.raises(ValueError):
            cb.call(fail)
    assert cb._recovery_timeout == RECOVERY_BACKOFF[0]  # 60s

    # Each subsequent probe failure increments consecutive and may raise backoff.
    # failures 4-6 → 120s
    for _ in range(3):
        current_time[0] += cb._recovery_timeout + 1.0
        with pytest.raises(ValueError):
            cb.call(fail)
    assert cb._recovery_timeout == RECOVERY_BACKOFF[1]  # 120s

    # failures 7-9 → 240s
    for _ in range(3):
        current_time[0] += cb._recovery_timeout + 1.0
        with pytest.raises(ValueError):
            cb.call(fail)
    assert cb._recovery_timeout == RECOVERY_BACKOFF[2]  # 240s

    # failures 10-12 → 480s
    for _ in range(3):
        current_time[0] += cb._recovery_timeout + 1.0
        with pytest.raises(ValueError):
            cb.call(fail)
    assert cb._recovery_timeout == RECOVERY_BACKOFF[3]  # 480s

    # failure 13 → 900s (cap)
    current_time[0] += cb._recovery_timeout + 1.0
    with pytest.raises(ValueError):
        cb.call(fail)
    assert cb._recovery_timeout == RECOVERY_BACKOFF[4]  # 900s


# ---------------------------------------------------------------------------
# B4 Test 13 — Cross-worker state sharing via _reload_state()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cross_worker_state_sharing(tmp_path):
    """_reload_state() in call() picks up OPEN state written by another CB instance.

    Simulates: CB2 is created when state is CLOSED, then CB1 opens the CB.
    CB2's next call() must reload state and fast-reject without calling fn.
    """
    from orchestrator.storage.lock import SqliteCircuitBreakerStore

    store = SqliteCircuitBreakerStore(tmp_path)

    # CB2 created while state is still CLOSED (nothing in DB yet)
    cb2 = CircuitBreaker("test_provider", store, failure_threshold=3)
    assert cb2.state is CircuitBreakerState.CLOSED

    # CB1 opens the CB via 3 failures
    cb1 = make_cb(threshold=3, store=store)
    for _ in range(3):
        with pytest.raises(ValueError):
            cb1.call(lambda: (_ for _ in ()).throw(ValueError("x")))  # type: ignore[misc]

    assert cb1.state is CircuitBreakerState.OPEN

    # CB2 was stale (CLOSED in memory), but call() reloads state and sees OPEN
    fn = MagicMock(return_value="ok")
    with pytest.raises(CircuitBreakerOpenError):
        cb2.call(fn)

    fn.assert_not_called()
    assert cb2.state is CircuitBreakerState.OPEN


# ---------------------------------------------------------------------------
# Test 14 — Concurrent HALF_OPEN admits exactly one probe
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_concurrent_half_open_exactly_one_probe(monkeypatch):
    """Under N concurrent calls into a HALF_OPEN CB, exactly one fn() runs.

    Regression test for the _half_open_in_flight TOCTOU race fixed by
    self._lock in _execute_half_open. The winning probe blocks in fn()
    so we can observe the losers rejecting under a stable in_flight=True
    state; without the lock, two or more threads could all observe
    in_flight=False and race into fn().
    """
    cb = make_cb(threshold=2, timeout=60.0)

    base_time = 1000.0
    current_time = [base_time]
    monkeypatch.setattr(time, "time", lambda: current_time[0])

    # Open the CB.
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("x")))  # type: ignore[misc]

    # Advance past recovery_timeout so call() will transition OPEN → HALF_OPEN.
    current_time[0] = base_time + 61.0

    n_threads = 8
    start_barrier = threading.Barrier(n_threads)
    probe_started = threading.Event()
    probe_can_finish = threading.Event()

    fn_run_count = 0
    fn_run_lock = threading.Lock()

    def probe_fn():
        nonlocal fn_run_count
        with fn_run_lock:
            fn_run_count += 1
        probe_started.set()
        # Block until the test releases the winner. Uses threading.Event
        # timing (monotonic clock), unaffected by the time.time monkeypatch.
        assert probe_can_finish.wait(timeout=5.0), "probe_can_finish never set"
        return "ok"

    results: list[str] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def worker():
        start_barrier.wait(timeout=5.0)
        try:
            r = cb.call(probe_fn)
        except CircuitBreakerOpenError as e:
            with results_lock:
                errors.append(e)
        else:
            with results_lock:
                results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()

    # Wait for the winning probe to enter fn().
    assert probe_started.wait(timeout=5.0), "no thread reached probe_fn"

    # Losers see _half_open_in_flight=True and raise immediately, so they
    # finish quickly. Wait until only the winner remains alive before
    # releasing the winner — this pins the CB in HALF_OPEN with
    # in_flight=True for the entire loser-rejection window.
    deadline = time.monotonic() + 5.0
    while sum(1 for t in threads if t.is_alive()) > 1 and time.monotonic() < deadline:
        time.sleep(0.001)

    probe_can_finish.set()
    for t in threads:
        t.join(timeout=5.0)

    assert fn_run_count == 1, f"expected exactly 1 fn() run, got {fn_run_count}"
    assert results == ["ok"]
    assert len(errors) == n_threads - 1
    assert cb.state is CircuitBreakerState.CLOSED
    assert cb._half_open_in_flight is False


# ---------------------------------------------------------------------------
# Test 15 — Concurrent failures increment counter without lost updates
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_concurrent_failures_consistent_count():
    """N threads concurrently failing produce exactly N counter increments.

    Regression test for the multi-field state-mutation race in _on_failure
    fixed by self._lock. Threshold is set high enough that the CB stays
    CLOSED throughout, isolating the lost-update scenario from the
    OPEN-transition logic.
    """
    n_threads = 20
    cb = make_cb(threshold=n_threads + 1)

    def fail():
        raise ValueError("x")

    def worker():
        with contextlib.suppress(ValueError):
            cb.call(fail)

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(worker) for _ in range(n_threads)]
        for f in futures:
            f.result()

    assert cb._consecutive_failures == n_threads
    assert cb.state is CircuitBreakerState.CLOSED


# ---------------------------------------------------------------------------
# Test 16 — SqliteCircuitBreakerStore cross-thread access
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sqlite_store_cross_thread(tmp_path):
    """Store created on main thread is safely used from worker threads.

    Regression test for the check_same_thread=False + _conn_lock fix.
    Before the fix, any worker-thread call to get_state()/set_state()
    raised sqlite3.ProgrammingError.
    """
    from orchestrator.storage.lock import SqliteCircuitBreakerStore

    store = SqliteCircuitBreakerStore(tmp_path)
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            store.set_state(f"p{i}", {"state": "CLOSED", "failures": i})
            result = store.get_state(f"p{i}")
            assert result is not None
            assert result["failures"] == i
        except Exception as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(worker, i) for i in range(4)]
        for f in futures:
            f.result()

    assert errors == [], f"cross-thread store access raised: {errors}"


# ---------------------------------------------------------------------------
# Test 17 — circuit_breaker_for() returns the same instance under contention
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_registry_singleton_under_contention():
    """8 threads racing circuit_breaker_for() for the same provider all get the same instance.

    Regression test for the _registry_lock fix. Before the fix, two threads
    could each pass the 'not in _registry' check and create two distinct
    CircuitBreaker objects with independent locks.
    """
    import orchestrator.circuit_breaker as cb_module

    provider = "test-provider-race"
    results: list[int] = []

    def worker() -> None:
        cb = cb_module.circuit_breaker_for(provider)
        results.append(id(cb))

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker) for _ in range(8)]
            for f in futures:
                f.result()

        assert len(set(results)) == 1, (
            f"expected 1 unique instance, got {len(set(results))}: {set(results)}"
        )
        assert provider in cb_module._registry
    finally:
        with cb_module._registry_lock:
            cb_module._registry.pop(provider, None)
