import logging
from unittest.mock import MagicMock

import pytest

from orchestrator.agents.executor.providers import ProviderChainResult
from orchestrator.agents.validator import run
from orchestrator.agents.validator.summarizer import _summarize_errors
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.validator_output import ToolResult, ValidatorOutput


@pytest.mark.unit
def test_validator_run_returns_tuple(monkeypatch, tmp_path, provider_runtime):
    result = ToolResult(tool="ruff", passed=True, return_code=0)
    monkeypatch.setattr("orchestrator.agents.validator.run_ruff", lambda *a, **kw: result)
    monkeypatch.setattr("orchestrator.agents.validator.run_pytest", lambda *a, **kw: result)
    monkeypatch.setattr("orchestrator.agents.validator.run_tsc", lambda *a, **kw: result)
    config = TargetConfig(
        target_path=tmp_path,
        workspace_path=tmp_path.parent / f"{tmp_path.name}-workspace",
    )

    output, meta = run(config=config, runtime=provider_runtime)

    assert isinstance(output, ValidatorOutput)
    assert isinstance(meta, dict)


@pytest.mark.unit
def test_validator_get_logger_uses_shared_helper(tmp_path, monkeypatch):
    import orchestrator.agents.validator as validator

    validator._logger = None
    for handler in list(logging.getLogger("validator").handlers):
        logging.getLogger("validator").removeHandler(handler)
        handler.close()
    mock = MagicMock(wraps=validator.get_file_logger)
    monkeypatch.setattr("orchestrator.agents.validator.get_file_logger", mock)

    validator._get_logger(tmp_path)

    mock.assert_called_once_with("validator", tmp_path, "validator.log")


def _failed_tool(stderr: str = "error") -> list[ToolResult]:
    return [ToolResult(tool="ruff", passed=False, return_code=1, stderr=stderr)]


@pytest.mark.unit
def test_summarizer_returns_provider_result(monkeypatch, provider_runtime):
    monkeypatch.setattr(
        "orchestrator.agents.validator.summarizer._call_chain",
        lambda *args, **kwargs: ProviderChainResult(
            success=("- ruff: syntax error", 10, 5, 0.0), provider_name="gemini"
        ),
    )

    summary, model = _summarize_errors(_failed_tool(), "run", provider_runtime)

    assert model == "gemini"
    assert "ruff" in summary


@pytest.mark.unit
def test_summarizer_fallback_result(monkeypatch, provider_runtime):
    monkeypatch.setattr(
        "orchestrator.agents.validator.summarizer._call_chain",
        lambda *args, **kwargs: ProviderChainResult(
            success=("- openrouter summary", 10, 5, 0.0), provider_name="openrouter"
        ),
    )

    summary, model = _summarize_errors(_failed_tool(), "run", provider_runtime)

    assert model == "openrouter"
    assert "openrouter summary" in summary


@pytest.mark.unit
def test_summarizer_all_fail_returns_raw(monkeypatch, provider_runtime):
    monkeypatch.setattr(
        "orchestrator.agents.validator.summarizer._call_chain",
        lambda *args, **kwargs: ProviderChainResult(),
    )

    summary, model = _summarize_errors(_failed_tool("some error"), "run", provider_runtime)

    assert model == ""
    assert "[ruff]" in summary
    assert "some error" in summary


@pytest.mark.unit
def test_summarizer_provider_lookup_failure_returns_raw(monkeypatch, provider_runtime):
    monkeypatch.setattr(
        "orchestrator.agents.validator.summarizer._provider_by_name",
        lambda: {},
    )

    summary, model = _summarize_errors(_failed_tool("lookup error"), "run", provider_runtime)

    assert model == ""
    assert summary == "[ruff] lookup error"
