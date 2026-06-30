from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_circuit_breakers(tmp_path):
    """Lazily init circuit breakers into a per-test temp dir so that
    (a) import-time SQLite races under xdist are avoided, and
    (b) production ~/.patchforge/coordination.db is never touched.

    After each test, reset CB state to prevent leakage across reuse.
    """
    from orchestrator.agents.executor import providers

    if providers._coord_store is None:
        from orchestrator.circuit_breaker import circuit_breaker_for
        from orchestrator.storage.lock import SqliteCircuitBreakerStore

        providers._coord_store = SqliteCircuitBreakerStore(tmp_path)
        providers._cb_gemini = circuit_breaker_for("gemini", store=providers._coord_store)
        providers._cb_openrouter = circuit_breaker_for("openrouter", store=providers._coord_store)
        providers._cb_claude = circuit_breaker_for("claude", store=providers._coord_store)

    yield
    try:
        from orchestrator import circuit_breaker
        from orchestrator.circuit_breaker import CircuitBreakerState

        for cb in list(circuit_breaker._registry.values()):
            cb._state = CircuitBreakerState.CLOSED
            cb._consecutive_failures = 0
            cb._last_failure_time = 0.0
            cb._half_open_in_flight = False
    except (ImportError, AttributeError):
        pass


@pytest.fixture
def mock_gemini(monkeypatch):
    mock = MagicMock()
    # Mock v2 path
    for path in ["orchestrator.agents.scout.call_gemini"]:
        try:
            monkeypatch.setattr(path, mock)
        except (AttributeError, ModuleNotFoundError):
            pass
    return mock


@pytest.fixture
def mock_claude(monkeypatch):
    mock = MagicMock()
    for path in ["orchestrator.agents.architect.call_claude"]:
        try:
            monkeypatch.setattr(path, mock)
        except (AttributeError, ModuleNotFoundError):
            pass
    return mock
