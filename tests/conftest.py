from unittest.mock import MagicMock

import pytest


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


@pytest.fixture
def mock_groq(monkeypatch):
    mock = MagicMock()
    for path in ["orchestrator.agents.executor.providers._call_groq"]:
        try:
            monkeypatch.setattr(path, mock)
        except (AttributeError, ModuleNotFoundError):
            pass
    return mock


@pytest.fixture
def mock_subprocess(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("subprocess.run", mock)
    return mock
