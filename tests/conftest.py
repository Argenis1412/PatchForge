from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    """Reset live CB objects between tests to prevent state leakage
    across xdist worker reuse. Targets the objects referenced module-level
    by providers.py and validator/__init__.py (not the _registry dict).

    Resets state in memory only (no _persist_state) so the production
    ~/.patchforge/coordination.db is not overwritten on each test run.
    """
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
