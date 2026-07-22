from pathlib import Path

import pytest

from orchestrator.agents.architect import (
    ARCHITECT_PROMPT,
    ISSUE_ARCHITECT_PROMPT,
    run,
    run_from_issue,
)
from orchestrator.agents.architect.provider import ArchitectCallResult
from orchestrator.llm.parser import LLMParseError
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.issue import IssueInput
from orchestrator.schemas.scout_output import ScoutOutput

_SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"


def _read_snapshot(name: str) -> str:
    return (_SNAPSHOT_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Snapshot tests — ensure prompts are not altered by the extraction
# ---------------------------------------------------------------------------


def test_architect_prompt_snapshot():
    assert _read_snapshot("ARCHITECT_PROMPT.txt") == ARCHITECT_PROMPT


def test_issue_architect_prompt_snapshot():
    assert _read_snapshot("ISSUE_ARCHITECT_PROMPT.txt") == ISSUE_ARCHITECT_PROMPT


_CLEAN_JSON = (
    '{"validated_findings": [], "false_positives": [],'
    ' "systemic_risks": [], "implementation_plan": [], "blockers": []}'
)


def _make_scout() -> ScoutOutput:
    return ScoutOutput(hotspots=[], summary="s", risks=["r"], recommended_order=[])


def _claude_result(raw, tokens, cost, model_used, **kw) -> ArchitectCallResult:
    return ArchitectCallResult(raw=raw, tokens=tokens, cost=cost, model_used=model_used, **kw)


# ---------------------------------------------------------------------------
# run() integration
# ---------------------------------------------------------------------------


class TestArchitectRun:
    @pytest.mark.unit
    def test_clean_json(self, mock_claude):
        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)
        assert isinstance(meta, dict)

    @pytest.mark.unit
    def test_trailing_text(self, mock_claude):
        raw = _CLEAN_JSON + "\n\nLet me know if you need adjustments."
        mock_claude.return_value = _claude_result(
            raw, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_preamble_text(self, mock_claude):
        raw = "Here is my analysis:\n" + _CLEAN_JSON
        mock_claude.return_value = _claude_result(
            raw, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_preamble_fenced_trailing(self, mock_claude):
        raw = "Preamble\n\n```json\n" + _CLEAN_JSON + "\n```\n\nTrailing text"
        mock_claude.return_value = _claude_result(
            raw, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_brace_in_string_value(self, mock_claude):
        raw = (
            '{"message": "contains } brace", "ok": true,'
            ' "validated_findings": [], "false_positives": [],'
            ' "systemic_risks": [], "implementation_plan": [], "blockers": []}'
        )
        mock_claude.return_value = _claude_result(
            raw, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_raises_on_invalid_json(self, mock_claude):
        mock_claude.return_value = _claude_result(
            "not json at all", {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        with pytest.raises(LLMParseError):
            run(_make_scout())

    @pytest.mark.unit
    def test_raises_on_empty_response(self, mock_claude):
        mock_claude.return_value = _claude_result(
            "", {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        with pytest.raises(LLMParseError):
            run(_make_scout())


# ---------------------------------------------------------------------------
# run_from_issue() integration
# ---------------------------------------------------------------------------


def _make_issue() -> IssueInput:
    raw = "---\ntitle: Test Issue\n---\nFix the thing"
    return IssueInput(title="Test Issue", body="Fix the thing", raw=raw)


class TestArchitectRunFromIssue:
    @pytest.mark.unit
    def test_clean_json(self, mock_claude):
        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        output, meta = run_from_issue(_make_issue())
        assert isinstance(output, ArchitectOutput)
        assert isinstance(meta, dict)

    @pytest.mark.unit
    def test_trailing_text(self, mock_claude):
        raw = _CLEAN_JSON + "\n\nLet me know if you need adjustments."
        mock_claude.return_value = _claude_result(
            raw, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        output, meta = run_from_issue(_make_issue())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_raises_on_invalid_json(self, mock_claude):
        mock_claude.return_value = _claude_result(
            "not json at all", {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        with pytest.raises(LLMParseError):
            run_from_issue(_make_issue())

    @pytest.mark.unit
    def test_braces_in_body_do_not_crash(self, mock_claude):
        issue = IssueInput(
            title="Braces",
            body="{x: 1, y: 2}",
            raw="---\ntitle: Braces\n---\n{x: 1, y: 2}",
        )
        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        output, meta = run_from_issue(issue)
        assert isinstance(output, ArchitectOutput)


# ---------------------------------------------------------------------------
# Fallback chain tests
# ---------------------------------------------------------------------------


class TestArchitectFallback:
    @pytest.mark.unit
    def test_fallback_model_used_in_meta(self, mock_claude):
        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 100, "output": 50}, 0.01, "gemini-2.5-flash"
        )
        _, meta = run(_make_scout())
        assert meta["model_used"] == "gemini-2.5-flash"

    @pytest.mark.unit
    def test_run_from_issue_fallback_model(self, mock_claude):
        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 100, "output": 50}, 0.01, "openrouter/free"
        )
        _, meta = run_from_issue(_make_issue())
        assert meta["model_used"] == "openrouter/free"

    @pytest.mark.unit
    def test_provider_error_propagates(self, mock_claude):
        from orchestrator.exceptions import ProviderError

        mock_claude.side_effect = ProviderError("provider_chain", "All providers failed")
        with pytest.raises(ProviderError):
            run(_make_scout())


# ---------------------------------------------------------------------------
# Provider chain unit tests — exercise architect/provider.py:call_claude directly
# ---------------------------------------------------------------------------


class TestArchitectProviderChain:
    @pytest.mark.unit
    def test_gemini_fallback_uses_registry_resolved_model_and_chain_cost(self, monkeypatch):
        """Issue #246: architect no longer recomputes cost locally — it uses
        whatever _call_chain already computed (via _compute_cost), and resolves
        model_used through the shared Provider Registry (_get_model)."""
        from orchestrator.agents.architect import provider as arch_provider
        from orchestrator.agents.executor.providers import ProviderChainResult

        chain_result = ProviderChainResult(
            success=(_CLEAN_JSON, 1_000_000, 1_000_000, 0.375),
            provider_name="gemini",
        )
        monkeypatch.setattr(arch_provider, "_call_chain", lambda *a, **kw: chain_result)
        monkeypatch.setattr(arch_provider, "log_call", lambda *a, **kw: None)

        result = arch_provider.call_claude("prompt", "architect")

        assert result.raw == _CLEAN_JSON
        assert result.model_used == "gemini-2.5-flash"
        assert result.cost == pytest.approx(0.375)
        assert result.tokens == {"input": 1_000_000, "output": 1_000_000}

    @pytest.mark.unit
    def test_claude_default_uses_registry_resolved_model_and_chain_cost(self, monkeypatch):
        from orchestrator.agents.architect import provider as arch_provider
        from orchestrator.agents.executor.providers import ProviderChainResult

        chain_result = ProviderChainResult(
            success=(_CLEAN_JSON, 1_000_000, 1_000_000, 18.0),
            provider_name="claude",
        )
        monkeypatch.setattr(arch_provider, "_call_chain", lambda *a, **kw: chain_result)
        monkeypatch.setattr(arch_provider, "log_call", lambda *a, **kw: None)

        result = arch_provider.call_claude("prompt", "architect")

        assert result.model_used == "claude-sonnet-4-6"
        assert result.cost == pytest.approx(18.0)

    @pytest.mark.unit
    def test_none_cost_propagates_when_model_overridden(self, monkeypatch):
        """AC6: when _call_chain reports cost=None (overridden model with an
        unknown cost table), architect must propagate None, not coerce to 0.0."""
        from orchestrator.agents.architect import provider as arch_provider
        from orchestrator.agents.executor.providers import ProviderChainResult

        chain_result = ProviderChainResult(
            success=(_CLEAN_JSON, 1_000_000, 1_000_000, None),
            provider_name="claude",
        )
        monkeypatch.setattr(arch_provider, "_call_chain", lambda *a, **kw: chain_result)
        logged_costs = []
        monkeypatch.setattr(
            arch_provider,
            "log_call",
            lambda *a, **kw: logged_costs.append(kw.get("cost_usd")),
        )

        result = arch_provider.call_claude("prompt", "architect")

        assert result.cost is None
        assert logged_costs == [None]

    @pytest.mark.unit
    def test_chain_exhausted_raises_provider_error(self, monkeypatch):
        from orchestrator.agents.architect import provider as arch_provider
        from orchestrator.agents.executor.providers import ProviderChainResult
        from orchestrator.exceptions import ProviderError

        chain_result = ProviderChainResult(
            success=None,
            failures=[("_call_claude", "down"), ("_call_gemini", "down")],
        )
        monkeypatch.setattr(arch_provider, "_call_chain", lambda *a, **kw: chain_result)
        monkeypatch.setattr(arch_provider, "log_failure", lambda *a, **kw: None)

        with pytest.raises(ProviderError):
            arch_provider.call_claude("prompt", "architect")

    @pytest.mark.unit
    def test_force_provider_builds_single_provider_chain(self, monkeypatch):
        from unittest.mock import MagicMock

        from orchestrator.agents.architect import provider as arch_provider
        from orchestrator.agents.executor.providers import (
            ProviderChainResult,
            _call_gemini,
        )

        chain_result = ProviderChainResult(
            success=(_CLEAN_JSON, 100, 50, 0.0),
            provider_name="gemini",
        )
        mock_call_chain = MagicMock(return_value=chain_result)
        monkeypatch.setattr(arch_provider, "_call_chain", mock_call_chain)
        monkeypatch.setattr(arch_provider, "log_call", lambda *a, **kw: None)

        arch_provider.call_claude("prompt", "architect", force_provider="gemini")

        chain_arg = mock_call_chain.call_args[0][0]
        assert len(chain_arg) == 1
        assert chain_arg[0] is _call_gemini

    @pytest.mark.unit
    def test_force_provider_invalid_raises_provider_error(self):
        from orchestrator.agents.architect import provider as arch_provider
        from orchestrator.exceptions import ProviderError

        with pytest.raises(ProviderError):
            arch_provider.call_claude("prompt", "architect", force_provider="nonexistent")


# ---------------------------------------------------------------------------
# Target files injection tests (D-001 root cause)
# ---------------------------------------------------------------------------


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _make_target_config(tmp_path: Path) -> TargetConfig:
    workspace = tmp_path.parent / "_workspace"
    workspace.mkdir(exist_ok=True)
    return TargetConfig(target_path=tmp_path, workspace_path=workspace)


class TestArchitectTargetFilesInjection:
    @pytest.mark.unit
    def test_run_prompt_contains_target_files(self, tmp_path, mock_claude):
        _touch(tmp_path / "src" / "main.py")
        _touch(tmp_path / "README.md")
        config = _make_target_config(tmp_path)

        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        run(_make_scout(), config)

        prompt_sent = mock_claude.call_args[0][0]
        assert "[TARGET FILES]" in prompt_sent
        assert "src/main.py" in prompt_sent
        assert "README.md" in prompt_sent

    @pytest.mark.unit
    def test_run_from_issue_prompt_contains_target_files(self, tmp_path, mock_claude):
        _touch(tmp_path / "src" / "app.py")
        config = _make_target_config(tmp_path)

        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        run_from_issue(_make_issue(), config)

        prompt_sent = mock_claude.call_args[0][0]
        assert "[TARGET FILES]" in prompt_sent
        assert "src/app.py" in prompt_sent

    @pytest.mark.unit
    def test_run_no_config_shows_unavailable(self, mock_claude):
        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        run(_make_scout(), config=None)

        prompt_sent = mock_claude.call_args[0][0]
        assert "[TARGET FILES]" in prompt_sent
        assert "(unavailable — no target config provided)" in prompt_sent

    @pytest.mark.unit
    def test_run_from_issue_no_config_shows_unavailable(self, mock_claude):
        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        run_from_issue(_make_issue(), config=None)

        prompt_sent = mock_claude.call_args[0][0]
        assert "[TARGET FILES]" in prompt_sent
        assert "(unavailable — no target config provided)" in prompt_sent

    @pytest.mark.unit
    def test_prompt_contains_path_constraint_instruction(self, mock_claude):
        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        run(_make_scout())

        prompt_sent = mock_claude.call_args[0][0]
        assert "IMPORTANT — path constraints:" in prompt_sent
        assert "Do NOT invent paths whose parent directory does not exist." in prompt_sent

    @pytest.mark.unit
    def test_run_from_issue_prompt_contains_path_constraint_instruction(self, mock_claude):
        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        run_from_issue(_make_issue())

        prompt_sent = mock_claude.call_args[0][0]
        assert "IMPORTANT — path constraints:" in prompt_sent
        assert "Do NOT invent paths whose parent directory does not exist." in prompt_sent

    @pytest.mark.unit
    def test_run_prompt_contains_annotations(self, tmp_path, mock_claude):
        pkg = tmp_path / "src" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            '"""Package entry."""\n\ndef run():\n    pass\n',
            encoding="utf-8",
        )
        (pkg / "helper.py").write_text("def assist():\n    pass\n", encoding="utf-8")
        config = _make_target_config(tmp_path)

        mock_claude.return_value = _claude_result(
            _CLEAN_JSON, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6"
        )
        run(_make_scout(), config)

        prompt_sent = mock_claude.call_args[0][0]
        assert "src/pkg/__init__.py  # Package entry. | run()" in prompt_sent
        assert "src/pkg/helper.py  # assist()" in prompt_sent
