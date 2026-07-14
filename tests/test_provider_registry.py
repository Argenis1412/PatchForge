"""Tests for the Provider Registry (issue #230): configurable LLM models via
orchestrator.json ``providers`` section, with hardcoded constants as fallback.
"""

import logging
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from orchestrator.agents.executor import providers as providers_mod
from orchestrator.agents.executor.providers import (
    MODEL_CLAUDE,
    MODEL_GEMINI,
    MODEL_OPENROUTER,
    _call_claude,
    _compute_cost,
    _do_claude_call,
    _do_gemini_call,
    _get_model,
    init_provider_models,
)
from orchestrator.schemas.config import ProviderModelConfig, ProvidersConfig, TargetConfig
from orchestrator.schemas.validator_output import ToolResult


def _config(tmp_path, providers: ProvidersConfig | None = None) -> TargetConfig:
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    kwargs = {"target_path": tmp_path, "workspace_path": workspace}
    if providers is not None:
        kwargs["providers"] = providers
    return TargetConfig(**kwargs)


@pytest.mark.unit
def test_default_path_no_overrides(tmp_path):
    """No providers key in config: resolved models equal the hardcoded defaults."""
    config = _config(tmp_path)
    resolved = init_provider_models(config)

    assert resolved == {
        "gemini": MODEL_GEMINI,
        "openrouter": MODEL_OPENROUTER,
        "claude": MODEL_CLAUDE,
    }
    assert _get_model("claude") == MODEL_CLAUDE

    cost = _compute_cost(_call_claude, 1000, 500, _get_model("claude"))
    assert isinstance(cost, float)
    assert cost > 0


@pytest.mark.unit
def test_gemini_override_resolves_and_reaches_sdk(tmp_path, monkeypatch):
    """A gemini override in config flows through to the actual SDK call."""
    config = _config(
        tmp_path,
        ProvidersConfig(gemini=ProviderModelConfig(model="gemini-2.5-pro")),
    )
    init_provider_models(config)
    assert _get_model("gemini") == "gemini-2.5-pro"

    mock_response = MagicMock()
    mock_response.text = "modified content"
    mock_response.usage_metadata = None

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    monkeypatch.setattr(
        "orchestrator.agents.executor.providers.get_gemini_client", lambda: mock_client
    )

    _do_gemini_call("some prompt", "run_test")

    _, kwargs = mock_client.models.generate_content.call_args
    assert kwargs["model"] == "gemini-2.5-pro"


@pytest.mark.unit
def test_claude_override_cost_llm_null_and_warning(tmp_path, caplog):
    """Overriding Claude's model produces a None cost and a warning, never a wrong number."""
    config = _config(
        tmp_path,
        ProvidersConfig(claude=ProviderModelConfig(model="claude-3-5-haiku-20241022")),
    )
    init_provider_models(config)
    assert _get_model("claude") == "claude-3-5-haiku-20241022"

    # The executor logger has propagate=False (writes to its own file handler),
    # so caplog must be attached to it directly to observe records.
    executor_logger = logging.getLogger("executor")
    executor_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger="executor"):
            cost = _compute_cost(_call_claude, 1000, 500, _get_model("claude"))
    finally:
        executor_logger.removeHandler(caplog.handler)

    assert cost is None
    assert any("overridden" in record.getMessage() for record in caplog.records)


@pytest.mark.unit
def test_claude_override_not_used_keeps_cost_llm_known(tmp_path, monkeypatch):
    """Claude override in config must not null cost_llm when Claude was never called."""
    from orchestrator.agents.executor import run
    from orchestrator.schemas.architect_output import ArchitectOutput, Task

    config = _config(
        tmp_path,
        ProvidersConfig(claude=ProviderModelConfig(model="claude-3-5-haiku-20241022")),
    )
    (tmp_path / "test.py").write_text("x = 1\n", encoding="utf-8")

    task = Task(
        task_id="t1",
        title="bump x",
        description="bump x",
        files_to_modify=["test.py"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )
    arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[task],
        blockers=[],
    )

    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.return_value = ("x = 2\n", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    _, meta = run(arch_out, config=config, staging_dir=tmp_path / "staging")

    assert meta["cost_llm"] is not None
    assert meta["cost_llm"] == meta["cost_usd"]


@pytest.mark.unit
def test_empty_providers_falls_back_to_defaults(tmp_path):
    """An explicit empty providers block behaves identically to an absent one."""
    config = _config(tmp_path, ProvidersConfig())
    resolved = init_provider_models(config)

    assert resolved == {
        "gemini": MODEL_GEMINI,
        "openrouter": MODEL_OPENROUTER,
        "claude": MODEL_CLAUDE,
    }


@pytest.mark.unit
def test_unknown_provider_name_rejected():
    """A typo'd provider name (e.g. 'gpt') must raise, not silently ignore."""
    with pytest.raises(ValidationError):
        ProvidersConfig(**{"gpt": {"model": "gpt-4o"}})


@pytest.mark.unit
def test_unknown_field_in_provider_config_rejected():
    """A typo'd field name (e.g. 'models') must raise, not silently ignore."""
    with pytest.raises(ValidationError):
        ProviderModelConfig(**{"models": "claude-3-5-haiku-20241022"})


@pytest.mark.unit
def test_claude_call_uses_resolved_model_in_sdk_args(tmp_path, monkeypatch):
    """The Anthropic SDK call must receive the overridden model, not the default."""
    config = _config(
        tmp_path,
        ProvidersConfig(claude=ProviderModelConfig(model="claude-3-5-haiku-20241022")),
    )
    init_provider_models(config)

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="patched code")]
    mock_response.usage.input_tokens = 100
    mock_response.usage.output_tokens = 50

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    monkeypatch.setattr(
        "orchestrator.agents.executor.providers.get_anthropic_client", lambda: mock_client
    )

    _do_claude_call("some prompt", "run_test")

    _, kwargs = mock_client.messages.create.call_args
    assert kwargs["model"] == "claude-3-5-haiku-20241022"


@pytest.mark.unit
def test_summarizer_uses_gemini_override(tmp_path, monkeypatch):
    """The validator's error summarizer must honor a gemini override from config."""
    from orchestrator.agents.validator.summarizer import _summarize_errors

    config = _config(
        tmp_path,
        ProvidersConfig(gemini=ProviderModelConfig(model="gemini-2.5-pro")),
    )
    init_provider_models(config)

    monkeypatch.setenv("GOOGLE_API_KEY", "fake")

    mock_response = MagicMock()
    mock_response.text = "- ruff: syntax error in main.py:10"
    mock_response.usage_metadata = None

    mock_cb = MagicMock()
    mock_cb.call.side_effect = lambda fn: fn()
    monkeypatch.setattr("orchestrator.agents.validator._cb_validator", mock_cb)

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    monkeypatch.setattr("orchestrator.clients.gemini_client.get_gemini_client", lambda: mock_client)

    failed = [ToolResult(tool="ruff", passed=False, return_code=1, stderr="error")]
    summary, model = _summarize_errors(failed, "test-run")

    assert model == "gemini-2.5-pro"
    _, kwargs = mock_client.models.generate_content.call_args
    assert kwargs["model"] == "gemini-2.5-pro"


@pytest.mark.unit
def test_get_model_falls_back_when_uninitialized():
    """Before init_provider_models is called, _get_model returns hardcoded defaults."""
    providers_mod._resolved_models = {}
    assert _get_model("gemini") == MODEL_GEMINI
    assert _get_model("openrouter") == MODEL_OPENROUTER
    assert _get_model("claude") == MODEL_CLAUDE


@pytest.mark.unit
def test_model_whitespace_is_stripped():
    """A model value with surrounding whitespace is normalized on load."""
    cfg = ProviderModelConfig(model="  claude-sonnet-4-6  ")
    assert cfg.model == "claude-sonnet-4-6"
