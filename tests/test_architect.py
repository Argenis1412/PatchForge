import pytest

from orchestrator.agents.architect import run, run_from_issue
from orchestrator.llm.parser import LLMParseError
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.issue import IssueInput
from orchestrator.schemas.scout_output import ScoutOutput

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
        mock_claude.return_value = (_CLEAN_JSON, {"input": 1, "output": 1}, 0.01)
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)
        assert isinstance(meta, dict)

    @pytest.mark.unit
    def test_trailing_text(self, mock_claude):
        raw = _CLEAN_JSON + "\n\nLet me know if you need adjustments."
        mock_claude.return_value = (raw, {"input": 1, "output": 1}, 0.01)
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_preamble_text(self, mock_claude):
        raw = "Here is my analysis:\n" + _CLEAN_JSON
        mock_claude.return_value = (raw, {"input": 1, "output": 1}, 0.01)
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_preamble_fenced_trailing(self, mock_claude):
        raw = "Preamble\n\n```json\n" + _CLEAN_JSON + "\n```\n\nTrailing text"
        mock_claude.return_value = (raw, {"input": 1, "output": 1}, 0.01)
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_brace_in_string_value(self, mock_claude):
        raw = (
            '{"message": "contains } brace", "ok": true,'
            ' "validated_findings": [], "false_positives": [],'
            ' "systemic_risks": [], "implementation_plan": [], "blockers": []}'
        )
        mock_claude.return_value = (raw, {"input": 1, "output": 1}, 0.01)
        output, meta = run(_make_scout())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_raises_on_invalid_json(self, mock_claude):
        mock_claude.return_value = ("not json at all", {"input": 1, "output": 1}, 0.01)
        with pytest.raises(LLMParseError):
            run(_make_scout())

    @pytest.mark.unit
    def test_raises_on_empty_response(self, mock_claude):
        mock_claude.return_value = ("", {"input": 1, "output": 1}, 0.01)
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
        mock_claude.return_value = (_CLEAN_JSON, {"input": 1, "output": 1}, 0.01)
        output, meta = run_from_issue(_make_issue())
        assert isinstance(output, ArchitectOutput)
        assert isinstance(meta, dict)

    @pytest.mark.unit
    def test_trailing_text(self, mock_claude):
        raw = _CLEAN_JSON + "\n\nLet me know if you need adjustments."
        mock_claude.return_value = (raw, {"input": 1, "output": 1}, 0.01)
        output, meta = run_from_issue(_make_issue())
        assert isinstance(output, ArchitectOutput)

    @pytest.mark.unit
    def test_raises_on_invalid_json(self, mock_claude):
        mock_claude.return_value = ("not json at all", {"input": 1, "output": 1}, 0.01)
        with pytest.raises(LLMParseError):
            run_from_issue(_make_issue())

    @pytest.mark.unit
    def test_braces_in_body_do_not_crash(self, mock_claude):
        issue = IssueInput(
            title="Braces",
            body="{x: 1, y: 2}",
            raw="---\ntitle: Braces\n---\n{x: 1, y: 2}",
        )
        mock_claude.return_value = (_CLEAN_JSON, {"input": 1, "output": 1}, 0.01)
        output, meta = run_from_issue(issue)
        assert isinstance(output, ArchitectOutput)
