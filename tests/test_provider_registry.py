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


@pytest.mark.unit
def test_target_config_load_reads_providers_from_json(tmp_path):
    """TargetConfig.load() merges the providers section from orchestrator.json."""
    import json

    (tmp_path / "orchestrator.json").write_text(
        json.dumps({"providers": {"gemini": {"model": "gemini-2.5-pro"}}}),
        encoding="utf-8",
    )
    config = TargetConfig.load(tmp_path)
    resolved = init_provider_models(config)

    assert resolved["gemini"] == "gemini-2.5-pro"
    assert _get_model("gemini") == "gemini-2.5-pro"
    assert resolved["claude"] == MODEL_CLAUDE
    assert resolved["openrouter"] == MODEL_OPENROUTER


@pytest.mark.unit
def test_init_provider_models_none_config():
    """Passing config=None returns hardcoded defaults."""
    resolved = init_provider_models(None)
    assert resolved == {
        "gemini": MODEL_GEMINI,
        "openrouter": MODEL_OPENROUTER,
        "claude": MODEL_CLAUDE,
    }


@pytest.mark.unit
def test_architect_run_calls_init_provider_models_before_llm_call(tmp_path, monkeypatch):
    """Issue #246: architect.run() must resolve the registry before calling the LLM,
    regardless of entry point — plan.py never did this before."""
    from orchestrator.agents.architect import run
    from orchestrator.schemas.scout_output import ScoutOutput

    config = _config(
        tmp_path,
        ProvidersConfig(claude=ProviderModelConfig(model="claude-3-5-haiku-20241022")),
    )
    scout_output = ScoutOutput(hotspots=[], summary="s", risks=["r"], recommended_order=[])

    calls: list = []
    original_init = init_provider_models

    def _spy_init(cfg):
        calls.append(cfg)
        return original_init(cfg)

    monkeypatch.setattr("orchestrator.agents.architect.init_provider_models", _spy_init)
    monkeypatch.setattr(
        "orchestrator.agents.architect.call_claude",
        lambda *a, **kw: (
            '{"validated_findings": [], "false_positives": [], "systemic_risks": [],'
            ' "implementation_plan": [], "blockers": []}',
            {"input": 1, "output": 1},
            None,
            "claude-3-5-haiku-20241022",
        ),
    )

    run(scout_output, config=config)

    assert calls == [config]
    assert _get_model("claude") == "claude-3-5-haiku-20241022"


@pytest.mark.unit
def test_architect_run_from_issue_uses_registry_resolved_model(tmp_path, monkeypatch):
    """run_from_issue() already receives config as a parameter — it must also
    resolve the registry, not just run()."""
    from orchestrator.agents.architect import run_from_issue
    from orchestrator.schemas.issue import IssueInput

    config = _config(
        tmp_path,
        ProvidersConfig(claude=ProviderModelConfig(model="claude-3-5-haiku-20241022")),
    )
    issue = IssueInput(title="t", severity="low", labels=[], body="b", raw="b")

    monkeypatch.setattr(
        "orchestrator.agents.architect.call_claude",
        lambda *a, **kw: (
            '{"validated_findings": [], "false_positives": [], "systemic_risks": [],'
            ' "implementation_plan": [], "blockers": []}',
            {"input": 1, "output": 1},
            None,
            "claude-3-5-haiku-20241022",
        ),
    )

    _, meta = run_from_issue(issue, config=config)

    assert meta["model_used"] == "claude-3-5-haiku-20241022"
    assert meta["cost_usd"] is None


@pytest.mark.unit
def test_architect_run_prints_unknown_cost_without_crash(tmp_path, monkeypatch, capsys):
    """AC10: the pre-implementation print must not crash with cost=None."""
    from orchestrator.agents.architect import run
    from orchestrator.schemas.scout_output import ScoutOutput

    config = _config(
        tmp_path,
        ProvidersConfig(claude=ProviderModelConfig(model="claude-3-5-haiku-20241022")),
    )
    scout_output = ScoutOutput(hotspots=[], summary="s", risks=["r"], recommended_order=[])

    monkeypatch.setattr(
        "orchestrator.agents.architect.call_claude",
        lambda *a, **kw: (
            '{"validated_findings": [], "false_positives": [], "systemic_risks": [],'
            ' "implementation_plan": [], "blockers": []}',
            {"input": 1, "output": 1},
            None,
            "claude-3-5-haiku-20241022",
        ),
    )

    _, meta = run(scout_output, config=config)

    assert meta["cost_usd"] is None
    assert "unknown" in capsys.readouterr().out


@pytest.mark.unit
def test_scout_run_calls_init_provider_models_before_llm_call(tmp_path, monkeypatch):
    """Issue #246: scout.run() must resolve the registry before calling Gemini."""
    from orchestrator.agents.scout import run

    config = _config(
        tmp_path,
        ProvidersConfig(gemini=ProviderModelConfig(model="gemini-2.5-pro")),
    )
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

    calls: list = []
    original_init = init_provider_models

    def _spy_init(cfg):
        calls.append(cfg)
        return original_init(cfg)

    monkeypatch.setattr("orchestrator.agents.scout.init_provider_models", _spy_init)
    scout_json = '{"hotspots": [], "summary": "s", "risks": [], "recommended_order": []}'
    monkeypatch.setattr(
        "orchestrator.agents.scout.call_gemini",
        lambda *a, **kw: (
            "[]" if kw.get("span_id") == "scout_pass1" else scout_json,
            {"input": 1, "output": 1},
            None,
            "gemini-2.5-pro",
        ),
    )

    run(config)

    assert len(calls) == 1
    assert calls[0].target_path == config.target_path
    assert _get_model("gemini") == "gemini-2.5-pro"


@pytest.mark.unit
def test_scout_run_none_cost_aggregation_does_not_crash(tmp_path, monkeypatch, capsys):
    """AC8: pass1 and pass2 both returning cost=None must not raise a TypeError
    when scout aggregates total cost, and prints must show 'unknown' not crash."""
    from orchestrator.agents.scout import run

    config = _config(
        tmp_path,
        ProvidersConfig(gemini=ProviderModelConfig(model="gemini-2.5-pro")),
    )
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

    scout_json = '{"hotspots": [], "summary": "s", "risks": [], "recommended_order": []}'
    monkeypatch.setattr(
        "orchestrator.agents.scout.call_gemini",
        lambda *a, **kw: (
            "[]" if kw.get("span_id") == "scout_pass1" else scout_json,
            {"input": 1, "output": 1},
            None,
            "gemini-2.5-pro",
        ),
    )

    output, meta = run(config)

    assert meta["cost_usd"] is None
    assert "unknown" in capsys.readouterr().out


@pytest.mark.unit
def test_openrouter_override_reaches_sdk(tmp_path, monkeypatch):
    """An openrouter override in config flows through to the HTTP payload."""
    config = _config(
        tmp_path,
        ProvidersConfig(openrouter=ProviderModelConfig(model="meta-llama/llama-4-scout")),
    )
    init_provider_models(config)
    assert _get_model("openrouter") == "meta-llama/llama-4-scout"

    from orchestrator.agents.executor.providers import _do_openrouter_call

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "patched"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    monkeypatch.setattr(
        "orchestrator.agents.executor.providers.get_openrouter_client",
        lambda: mock_client,
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")

    _do_openrouter_call("some prompt", "run_test")

    _, kwargs = mock_client.post.call_args
    assert kwargs["json"]["model"] == "meta-llama/llama-4-scout"


@pytest.mark.unit
def test_claude_override_used_end_to_end_cost_llm_null(tmp_path, monkeypatch):
    """executor.run() with Claude override + Claude actually called → cost_llm is None."""
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
        risk_level="high",
        dependencies=[],
    )
    arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[task],
        blockers=[],
    )

    cb_claude_mock = MagicMock()
    cb_claude_mock.call.return_value = ("x = 2\n", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_claude", cb_claude_mock)

    warnings_logged: list[str] = []
    import orchestrator.agents.executor as _emod

    original_get_logger = _emod._get_logger

    def _patched_get_logger(logs_dir=None):
        logger = original_get_logger(logs_dir)
        original_warning = logger.warning

        def _capture_warning(msg, *args, **kwargs):
            warnings_logged.append(msg % args if args else msg)
            return original_warning(msg, *args, **kwargs)

        logger.warning = _capture_warning
        return logger

    monkeypatch.setattr(_emod, "_get_logger", _patched_get_logger)

    _, meta = run(arch_out, config=config, staging_dir=tmp_path / "staging")

    assert meta["cost_llm"] is None
    assert any("Claude cost unknown" in w for w in warnings_logged)
