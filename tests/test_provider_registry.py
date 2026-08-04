"""Tests for invocation-scoped provider model and client configuration."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from orchestrator.agents.executor.providers import (
    _call_claude,
    _compute_cost,
    _do_claude_call,
    _do_gemini_call,
    _do_openrouter_call,
)
from orchestrator.clients.credentials import resolve_operator_credentials
from orchestrator.provider_runtime import (
    MODEL_CLAUDE,
    MODEL_GEMINI,
    MODEL_OPENROUTER,
    ProviderRuntime,
)
from orchestrator.schemas.config import ProviderModelConfig, ProvidersConfig, TargetConfig


def _config(tmp_path, providers: ProvidersConfig | None = None) -> TargetConfig:
    kwargs = {
        "target_path": tmp_path,
        "workspace_path": tmp_path.parent / f"{tmp_path.name}-workspace",
    }
    if providers is not None:
        kwargs["providers"] = providers
    return TargetConfig(**kwargs)


def _runtime(tmp_path, config: TargetConfig, **credentials: str) -> ProviderRuntime:
    environment = {
        "ANTHROPIC_API_KEY": credentials.get("claude", "test-anthropic"),
        "GOOGLE_API_KEY": credentials.get("gemini", "test-google"),
        "OPENROUTER_API_KEY": credentials.get("openrouter", "test-openrouter"),
    }
    context = resolve_operator_credentials(target_path=tmp_path, inherited_environment=environment)
    return ProviderRuntime.from_config(context, config)


@pytest.mark.unit
def test_runtime_uses_default_models(tmp_path):
    runtime = _runtime(tmp_path, _config(tmp_path))
    assert dict(runtime.models) == {
        "gemini": MODEL_GEMINI,
        "openrouter": MODEL_OPENROUTER,
        "claude": MODEL_CLAUDE,
    }
    assert _compute_cost(_call_claude, 1000, 500, runtime.model_for("claude")) > 0


@pytest.mark.unit
def test_runtime_resolves_model_overrides_without_shared_state(tmp_path):
    first = _runtime(
        tmp_path,
        _config(
            tmp_path,
            ProvidersConfig(gemini=ProviderModelConfig(model="gemini-2.5-pro")),
        ),
    )
    second = _runtime(tmp_path, _config(tmp_path))
    assert first.model_for("gemini") == "gemini-2.5-pro"
    assert second.model_for("gemini") == MODEL_GEMINI


@pytest.mark.unit
def test_gemini_override_reaches_sdk(tmp_path, monkeypatch):
    runtime = _runtime(
        tmp_path,
        _config(
            tmp_path,
            ProvidersConfig(gemini=ProviderModelConfig(model="gemini-2.5-pro")),
        ),
    )
    response = MagicMock(text="modified content", usage_metadata=None)
    client = MagicMock()
    client.__enter__.return_value = client
    client.models.generate_content.return_value = response
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("orchestrator.agents.executor.providers.create_gemini_client", factory)

    _do_gemini_call(runtime, "prompt", "run")

    factory.assert_called_once_with("test-google")
    client.__exit__.assert_called_once()
    assert client.models.generate_content.call_args.kwargs["model"] == "gemini-2.5-pro"


@pytest.mark.unit
def test_claude_override_reaches_sdk_and_has_unknown_cost(tmp_path, monkeypatch):
    runtime = _runtime(
        tmp_path,
        _config(
            tmp_path,
            ProvidersConfig(claude=ProviderModelConfig(model="claude-haiku")),
        ),
    )
    response = MagicMock()
    response.content = [MagicMock(text="patched")]
    response.usage.input_tokens = 10
    response.usage.output_tokens = 5
    client = MagicMock()
    client.__enter__.return_value = client
    client.messages.create.return_value = response
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("orchestrator.agents.executor.providers.create_anthropic_client", factory)

    _do_claude_call(runtime, "prompt", "run")

    factory.assert_called_once_with("test-anthropic")
    client.__exit__.assert_called_once()
    assert client.messages.create.call_args.kwargs["model"] == "claude-haiku"
    assert _compute_cost(_call_claude, 10, 5, runtime.model_for("claude")) is None


@pytest.mark.unit
def test_openrouter_override_reaches_sdk(tmp_path, monkeypatch):
    runtime = _runtime(
        tmp_path,
        _config(
            tmp_path,
            ProvidersConfig(openrouter=ProviderModelConfig(model="custom/openrouter")),
        ),
    )
    response = MagicMock()
    response.json.return_value = {
        "choices": [{"message": {"content": "patched"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = response
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("orchestrator.agents.executor.providers.create_openrouter_client", factory)

    _do_openrouter_call(runtime, "prompt", "run")

    factory.assert_called_once_with("test-openrouter")
    client.__exit__.assert_called_once()
    assert client.post.call_args.kwargs["json"]["model"] == "custom/openrouter"


@pytest.mark.unit
def test_provider_configuration_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ProvidersConfig(**{"gpt": {"model": "gpt-4o"}})
    with pytest.raises(ValidationError):
        ProviderModelConfig(**{"models": "claude"})


@pytest.mark.unit
def test_model_whitespace_is_stripped():
    assert ProviderModelConfig(model="  claude-sonnet-4-6  ").model == MODEL_CLAUDE
