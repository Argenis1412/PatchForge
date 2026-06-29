import logging
from unittest.mock import MagicMock

import pytest

from orchestrator.agents.validator import run
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.validator_output import ToolResult, ValidatorOutput


@pytest.mark.unit
def test_validator_run_returns_tuple(monkeypatch, tmp_path):
    mock_tool = ToolResult(tool="ruff", passed=True, return_code=0)
    monkeypatch.setattr(
        "orchestrator.agents.validator.run_ruff",
        lambda *a, **kw: mock_tool,
    )
    monkeypatch.setattr(
        "orchestrator.agents.validator.run_pytest",
        lambda *a, **kw: mock_tool,
    )
    monkeypatch.setattr(
        "orchestrator.agents.validator.run_tsc",
        lambda *a, **kw: mock_tool,
    )
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
    output, meta = run(config=config)
    assert isinstance(output, ValidatorOutput)
    assert isinstance(meta, dict)


@pytest.mark.unit
def test_validator_get_logger_uses_shared_helper(tmp_path, monkeypatch):
    import orchestrator.agents.validator as val_mod

    val_mod._logger = None
    for h in list(logging.getLogger("validator").handlers):
        logging.getLogger("validator").removeHandler(h)
        h.close()

    mock = MagicMock(wraps=val_mod.get_file_logger)
    monkeypatch.setattr("orchestrator.agents.validator.get_file_logger", mock)

    val_mod._get_logger(tmp_path)
    mock.assert_called_once_with("validator", tmp_path, "validator.log")


# ---------------------------------------------------------------------------
# Summarizer fallback tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_summarizer_returns_tuple(monkeypatch):
    from orchestrator.agents.validator.summarizer import _summarize_errors

    monkeypatch.setenv("GOOGLE_API_KEY", "fake")

    mock_response = MagicMock()
    mock_response.text = "- ruff: syntax error in main.py:10"
    mock_response.usage_metadata = None

    mock_cb = MagicMock()
    mock_cb.call.return_value = mock_response
    monkeypatch.setattr("orchestrator.agents.validator._cb_validator", mock_cb)

    failed = [ToolResult(tool="ruff", passed=False, return_code=1, stderr="error")]
    summary, model = _summarize_errors(failed, "test-run")
    assert model == "gemini-2.5-flash"
    assert "ruff" in summary


@pytest.mark.unit
def test_summarizer_openrouter_fallback(monkeypatch):
    from orchestrator.agents.executor.providers import ProviderChainResult
    from orchestrator.agents.validator.summarizer import _summarize_errors
    from orchestrator.exceptions import CircuitBreakerOpenError

    monkeypatch.setenv("GOOGLE_API_KEY", "fake")

    mock_cb = MagicMock()
    mock_cb.call.side_effect = CircuitBreakerOpenError("gemini", MagicMock(), 0.0)
    monkeypatch.setattr("orchestrator.agents.validator._cb_validator", mock_cb)

    chain_result = ProviderChainResult(
        success=("- openrouter summary", 10, 5, 0.0),
        provider_name="openrouter",
    )
    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._call_chain",
        lambda chain, prompt, run_id: chain_result,
    )

    failed = [ToolResult(tool="ruff", passed=False, return_code=1, stderr="error")]
    summary, model = _summarize_errors(failed, "test-run")
    assert model == "openrouter/free"
    assert "openrouter summary" in summary


@pytest.mark.unit
def test_summarizer_all_fail_returns_raw(monkeypatch):
    from orchestrator.agents.validator.summarizer import _summarize_errors
    from orchestrator.exceptions import CircuitBreakerOpenError

    monkeypatch.setenv("GOOGLE_API_KEY", "fake")

    mock_cb = MagicMock()
    mock_cb.call.side_effect = CircuitBreakerOpenError("gemini", MagicMock(), 0.0)
    monkeypatch.setattr("orchestrator.agents.validator._cb_validator", mock_cb)

    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._call_chain",
        MagicMock(side_effect=Exception("OpenRouter down")),
    )

    failed = [ToolResult(tool="ruff", passed=False, return_code=1, stderr="some error")]
    summary, model = _summarize_errors(failed, "test-run")
    assert model == ""
    assert "[ruff]" in summary


@pytest.mark.unit
def test_summarizer_generic_exception_openrouter_fallback(monkeypatch):
    """A non-CB exception from Gemini still falls through to OpenRouter."""
    from orchestrator.agents.executor.providers import ProviderChainResult
    from orchestrator.agents.validator.summarizer import _summarize_errors

    monkeypatch.setenv("GOOGLE_API_KEY", "fake")

    mock_cb = MagicMock()
    mock_cb.call.side_effect = RuntimeError("gemini transport error")
    monkeypatch.setattr("orchestrator.agents.validator._cb_validator", mock_cb)

    chain_result = ProviderChainResult(
        success=("- openrouter summary", 10, 5, 0.0),
        provider_name="openrouter",
    )
    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._call_chain",
        lambda chain, prompt, run_id: chain_result,
    )

    failed = [ToolResult(tool="ruff", passed=False, return_code=1, stderr="error")]
    summary, model = _summarize_errors(failed, "test-run")
    assert model == "openrouter/free"
    assert "openrouter summary" in summary


@pytest.mark.unit
def test_summarizer_generic_exception_all_fail_returns_raw(monkeypatch):
    """Non-CB Gemini failure + OpenRouter failure degrades to raw stderr."""
    from orchestrator.agents.validator.summarizer import _summarize_errors

    monkeypatch.setenv("GOOGLE_API_KEY", "fake")

    mock_cb = MagicMock()
    mock_cb.call.side_effect = RuntimeError("gemini transport error")
    monkeypatch.setattr("orchestrator.agents.validator._cb_validator", mock_cb)

    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._call_chain",
        MagicMock(side_effect=Exception("OpenRouter down")),
    )

    failed = [ToolResult(tool="ruff", passed=False, return_code=1, stderr="some error")]
    summary, model = _summarize_errors(failed, "test-run")
    assert model == ""
    assert "[ruff]" in summary
