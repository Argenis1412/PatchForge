"""
tests/test_circuit_breaker_integration.py

2 integration tests using MagicMock to replace CB singletons.
No real LLM calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orchestrator.circuit_breaker import CircuitBreakerState
from orchestrator.exceptions import CircuitBreakerOpenError

# ---------------------------------------------------------------------------
# Test 1 — Executor falls back to Groq when Gemini CB is OPEN
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_executor_falls_back_when_primary_open(monkeypatch, tmp_path):
    """
    When _cb_gemini is OPEN and rejects the call, a LOW-risk task must
    automatically fall back to Groq. The result status must be 'applied'.
    """
    from orchestrator.schemas.architect_output import ArchitectOutput, Task
    from orchestrator.schemas.config import TargetConfig

    # ---- set up the source file ----
    source_file = tmp_path / "hello.py"
    source_file.write_text("x = 1\n", encoding="utf-8")

    task = Task(
        task_id="t-cb-01",
        title="bump x",
        description="change x to 2",
        files_to_modify=["hello.py"],
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
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
    staging_dir = tmp_path / "staging"

    # ---- mock: Gemini CB is OPEN → raises CircuitBreakerOpenError ----
    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = CircuitBreakerOpenError(
        provider="gemini",
        state=CircuitBreakerState.OPEN,
        retry_after=999_999.0,
    )
    monkeypatch.setattr("orchestrator.agents.executor._cb_gemini", cb_gemini_mock)

    # ---- mock: Groq CB succeeds ----
    cb_groq_mock = MagicMock()
    cb_groq_mock.call.return_value = ("x = 2\n", 10, 20)
    monkeypatch.setattr("orchestrator.agents.executor._cb_groq", cb_groq_mock)

    from orchestrator.agents.executor import run

    output, meta = run(arch_out, config=config, staging_dir=staging_dir)

    # Gemini CB was called (rejected)
    cb_gemini_mock.call.assert_called_once()
    # Groq CB was called as fallback
    cb_groq_mock.call.assert_called_once()

    # Task must have been applied (not errored)
    assert len(output.applied) == 1, f"Expected 1 applied, got errors={output.errors}"
    assert output.errors == []
    assert output.applied[0].task_id == "t-cb-01"


# ---------------------------------------------------------------------------
# Test 2 — Validator returns raw stderr when Gemini CB is OPEN
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_validator_uses_raw_stderr_when_cb_open(monkeypatch):
    """
    When _cb_validator raises CircuitBreakerOpenError, _summarize_errors
    must return the raw stderr fallback string — not an LLM summary.
    """

    from orchestrator.agents.validator import _summarize_errors
    from orchestrator.schemas.validator_output import ToolResult

    # Ensure GOOGLE_API_KEY is set so the function doesn't early-return
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-test")

    # ---- mock: validator CB is OPEN ----
    cb_mock = MagicMock()
    cb_mock.call.side_effect = CircuitBreakerOpenError(
        provider="gemini",
        state=CircuitBreakerState.OPEN,
        retry_after=999_999.0,
    )
    monkeypatch.setattr("orchestrator.agents.validator._cb_validator", cb_mock)

    # ---- create a failed tool result with identifiable stderr ----
    failed_tool = ToolResult(
        tool="ruff",  # type: ignore[arg-type]
        passed=False,
        return_code=1,
        stderr="E501 line too long at hello.py:42",
        stdout="",
    )

    result = _summarize_errors([failed_tool], run_id="test-run-id")

    # CB was invoked
    cb_mock.call.assert_called_once()

    # Result must be the raw stderr fallback, not an LLM response
    assert "ruff" in result
    assert "E501" in result
