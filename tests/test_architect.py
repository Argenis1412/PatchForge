from pathlib import Path

import pytest

from orchestrator.agents.architect import (
    ARCHITECT_PROMPT,
    ISSUE_ARCHITECT_PROMPT,
    run,
    run_from_issue,
)
from orchestrator.llm.parser import LLMParseError
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.issue import IssueInput
from orchestrator.schemas.scout_output import ScoutOutput

_SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"


def _read_snapshot(name: str) -> str:
    return (_SNAPSHOT_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Snapshot tests — ensure prompts are not altered by the extraction
# ---------------------------------------------------------------------------


def test_architect_prompt_snapshot():
    assert ARCHITECT_PROMPT == _read_snapshot("ARCHITECT_PROMPT.txt")


def test_issue_architect_prompt_snapshot():
    assert ISSUE_ARCHITECT_PROMPT == _read_snapshot("ISSUE_ARCHITECT_PROMPT.txt")


_CLEAN_JSON = (
    '{"validated_findings": [], "false_positives": [],'
    ' "systemic_risks": [], "implementation_plan": [], "blockers": []}'
)


def _make_scout() -> ScoutOutput:
    return ScoutOutput(hotspots=[], summary="s", risks=["r"], recommended_order=[])


# ---------------------------------------------------------------------------
# run() integration
# ---------------------------------------------------------------------------


class TestArchitectRun:
    @pytest.mark.unit
    def test_clean_json(self, mock_claude):
        mock_claude.return_value = (
            _CLEAN_JSON,
            {"input": 1, "output": 1},
            0.01,
            "claude-sonnet-4-6",
        )
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)
        assert isinstance(meta, dict)

    @pytest.mark.unit
    def test_trailing_text(self, mock_claude):
        raw = _CLEAN_JSON + "\n\nLet me know if you need adjustments."
        mock_claude.return_value = (raw, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6")
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_preamble_text(self, mock_claude):
        raw = "Here is my analysis:\n" + _CLEAN_JSON
        mock_claude.return_value = (raw, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6")
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_preamble_fenced_trailing(self, mock_claude):
        raw = "Preamble\n\n```json\n" + _CLEAN_JSON + "\n```\n\nTrailing text"
        mock_claude.return_value = (raw, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6")
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_brace_in_string_value(self, mock_claude):
        raw = (
            '{"message": "contains } brace", "ok": true,'
            ' "validated_findings": [], "false_positives": [],'
            ' "systemic_risks": [], "implementation_plan": [], "blockers": []}'
        )
        mock_claude.return_value = (raw, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6")
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_raises_on_invalid_json(self, mock_claude):
        mock_claude.return_value = (
            "not json at all",
            {"input": 1, "output": 1},
            0.01,
            "claude-sonnet-4-6",
        )
        with pytest.raises(LLMParseError):
            run(_make_scout())

    @pytest.mark.unit
    def test_raises_on_empty_response(self, mock_claude):
        mock_claude.return_value = ("", {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6")
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
        mock_claude.return_value = (
            _CLEAN_JSON,
            {"input": 1, "output": 1},
            0.01,
            "claude-sonnet-4-6",
        )
        output, meta = run_from_issue(_make_issue())
        assert isinstance(output, ArchitectOutput)
        assert isinstance(meta, dict)

    @pytest.mark.unit
    def test_trailing_text(self, mock_claude):
        raw = _CLEAN_JSON + "\n\nLet me know if you need adjustments."
        mock_claude.return_value = (raw, {"input": 1, "output": 1}, 0.01, "claude-sonnet-4-6")
        output, meta = run_from_issue(_make_issue())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_raises_on_invalid_json(self, mock_claude):
        mock_claude.return_value = (
            "not json at all",
            {"input": 1, "output": 1},
            0.01,
            "claude-sonnet-4-6",
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
        mock_claude.return_value = (
            _CLEAN_JSON,
            {"input": 1, "output": 1},
            0.01,
            "claude-sonnet-4-6",
        )
        output, meta = run_from_issue(issue)
        assert isinstance(output, ArchitectOutput)


# ---------------------------------------------------------------------------
# Fallback chain tests
# ---------------------------------------------------------------------------


class TestArchitectFallback:
    @pytest.mark.unit
    def test_fallback_model_used_in_meta(self, mock_claude):
        mock_claude.return_value = (
            _CLEAN_JSON,
            {"input": 100, "output": 50},
            0.01,
            "gemini-2.5-flash",
        )
        _, meta = run(_make_scout())
        assert meta["model_used"] == "gemini-2.5-flash"

    @pytest.mark.unit
    def test_run_from_issue_fallback_model(self, mock_claude):
        mock_claude.return_value = (
            _CLEAN_JSON,
            {"input": 100, "output": 50},
            0.01,
            "openrouter/free",
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
    def test_gemini_fallback_uses_gemini_cost_rates(self, monkeypatch):
        from orchestrator.agents.architect import provider as arch_provider
        from orchestrator.agents.executor.providers import ProviderChainResult

        chain_result = ProviderChainResult(
            success=(_CLEAN_JSON, 1_000_000, 1_000_000, 0.0),
            provider_name="gemini",
        )
        monkeypatch.setattr(arch_provider, "_call_chain", lambda *a, **kw: chain_result)
        monkeypatch.setattr(arch_provider, "log_call", lambda *a, **kw: None)

        raw, tokens, cost, model_used = arch_provider.call_claude("prompt", "architect")

        assert raw == _CLEAN_JSON
        assert model_used == "gemini-2.5-flash"
        # Gemini rates: 0.075 in + 0.30 out per 1M tokens
        assert cost == pytest.approx(0.075 + 0.30)
        assert tokens == {"input": 1_000_000, "output": 1_000_000}

    @pytest.mark.unit
    def test_claude_default_cost_rates(self, monkeypatch):
        from orchestrator.agents.architect import provider as arch_provider
        from orchestrator.agents.executor.providers import ProviderChainResult

        chain_result = ProviderChainResult(
            success=(_CLEAN_JSON, 1_000_000, 1_000_000, 0.0),
            provider_name="claude",
        )
        monkeypatch.setattr(arch_provider, "_call_chain", lambda *a, **kw: chain_result)
        monkeypatch.setattr(arch_provider, "log_call", lambda *a, **kw: None)

        _, _, cost, model_used = arch_provider.call_claude("prompt", "architect")

        assert model_used == "claude-sonnet-4-6"
        # Claude rates: 3.00 in + 15.00 out per 1M tokens
        assert cost == pytest.approx(3.00 + 15.00)

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
