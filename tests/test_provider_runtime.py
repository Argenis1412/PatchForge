from __future__ import annotations

import json
from unittest.mock import MagicMock

from orchestrator.agents.executor.providers import _call_chain
from orchestrator.clients.credentials import resolve_operator_credentials
from orchestrator.provider_runtime import ProviderRuntime
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.config import TargetConfig


def _runtime(tmp_path, environment: dict[str, str]) -> ProviderRuntime:
    context = resolve_operator_credentials(target_path=tmp_path, inherited_environment=environment)
    return ProviderRuntime.from_config(context, None)


def test_static_ineligible_provider_is_skipped_before_call(tmp_path):
    runtime = _runtime(tmp_path, {"ANTHROPIC_API_KEY": "claude-secret"})
    calls: list[str] = []
    skipped: list[str] = []

    def _call_gemini(runtime, prompt, run_id):
        calls.append("gemini")
        raise AssertionError("statically ineligible provider was invoked")

    def _call_claude(runtime, prompt, run_id):
        calls.append("claude")
        return "ok", 1, 1

    result = _call_chain(
        [_call_gemini, _call_claude],
        runtime,
        "prompt",
        "run",
        on_static_skip=skipped.append,
    )

    assert result.success is not None
    assert calls == ["claude"]
    assert skipped == ["gemini"]
    assert result.primary_provider_attempted == "claude"
    assert result.static_skipped_providers == ("gemini",)


def test_consecutive_runtimes_create_clients_with_only_their_credentials(tmp_path, monkeypatch):
    from orchestrator.agents.executor.providers import _do_gemini_call

    first = _runtime(tmp_path, {"GOOGLE_API_KEY": "first-secret"})
    second = _runtime(tmp_path, {"GOOGLE_API_KEY": "second-secret"})
    response = MagicMock(text="ok", usage_metadata=None)
    clients = [MagicMock(), MagicMock()]
    for client in clients:
        client.__enter__.return_value = client
        client.models.generate_content.return_value = response
    factory = MagicMock(side_effect=clients)
    monkeypatch.setattr("orchestrator.agents.executor.providers.create_gemini_client", factory)
    monkeypatch.setenv("GOOGLE_API_KEY", "global-secret")

    _do_gemini_call(first, "prompt", "first")
    _do_gemini_call(second, "prompt", "second")

    assert factory.call_args_list == [
        (("first-secret",), {}),
        (("second-secret",), {}),
    ]
    assert clients[0] is not clients[1]
    assert all(client.__exit__.call_count == 1 for client in clients)


def test_llm_client_modules_do_not_read_process_environment():
    from orchestrator.clients import anthropic_client, gemini_client, openrouter_client

    for module in (anthropic_client, gemini_client, openrouter_client):
        source = module.__loader__.get_source(module.__name__)
        assert "os.environ" not in source
        assert "os.getenv" not in source


def test_static_skip_event_contains_only_provider_and_cause(tmp_path, monkeypatch):
    from orchestrator.agents.executor import run

    target = tmp_path / "target"
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "run"
    target.mkdir()
    run_dir.mkdir(parents=True)
    (target / "module.py").write_text("value = 1\n", encoding="utf-8")
    config = TargetConfig(target_path=target, workspace_path=workspace)
    runtime = _runtime(target, {"ANTHROPIC_API_KEY": "claude-secret"})
    claude = MagicMock()
    claude.call.return_value = ("value = 2\n", 1, 1)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_claude", claude)
    plan = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[
            Task(
                task_id="task",
                title="change value",
                description="change value",
                files_to_modify=["module.py"],
                priority="low",
                effort="low",
                risk_level="low",
                dependencies=[],
            )
        ],
        blockers=[],
    )

    run(plan, run_id="run", config=config, run_dir=run_dir, runtime=runtime)

    entries = [
        json.loads(line)
        for line in (workspace / "logs" / "pipeline.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    skips = [entry for entry in entries if entry["event"] == "provider_skipped_static_ineligible"]
    assert [entry["data"]["provider"] for entry in skips] == ["gemini", "openrouter"]
    assert all(
        entry["data"]
        == {
            "provider": entry["data"]["provider"],
            "cause": "static_ineligible",
        }
        for entry in skips
    )
