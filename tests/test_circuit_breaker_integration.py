"""
tests/test_circuit_breaker_integration.py

Integration tests using MagicMock to replace CB singletons.
No real LLM calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orchestrator.circuit_breaker import CircuitBreakerState
from orchestrator.exceptions import CircuitBreakerOpenError
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.config import TargetConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(risk_level: str, task_id: str = "t-cb-01") -> Task:
    return Task(
        task_id=task_id,
        title="bump x",
        description="change x to 2",
        files_to_modify=["hello.py"],
        priority="high",
        effort="low",
        risk_level=risk_level,
        dependencies=[],
    )


def _make_arch_out(tasks) -> ArchitectOutput:
    return ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=tasks,
        blockers=[],
    )


def _run(tmp_path, arch_out, staging_dir=None):
    from orchestrator.agents.executor import run

    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
    return run(arch_out, config=config, staging_dir=staging_dir or tmp_path / "staging")


# ---------------------------------------------------------------------------
# Test 1 — Low risk: Gemini CB open → OpenRouter succeeds (1 hop)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_low_fallback_to_openrouter(monkeypatch, tmp_path):
    """Gemini CB open → OpenRouter succeeds."""
    (tmp_path / "hello.py").write_text("x = 1\n", encoding="utf-8")
    arch_out = _make_arch_out([_make_task("low")])

    cb_gemini = MagicMock()
    cb_gemini.call.side_effect = CircuitBreakerOpenError(
        provider="gemini",
        state=CircuitBreakerState.OPEN,
        retry_after=999_999.0,
    )
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini)

    cb_openrouter = MagicMock()
    cb_openrouter.call.return_value = ("x = 2\n", 10, 20)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_openrouter", cb_openrouter)

    output, _ = _run(tmp_path, arch_out)

    cb_gemini.call.assert_called_once()
    cb_openrouter.call.assert_called_once()
    assert len(output.applied) == 1, f"errors={output.errors}"
    assert output.applied[0].task_id == "t-cb-01"


# ---------------------------------------------------------------------------
# Test 2 — Low risk: Gemini CB open → OpenRouter CB open → Claude succeeds (2 hops)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_low_fallback_openrouter_then_claude(monkeypatch, tmp_path):
    """Gemini CB open → OpenRouter CB open → Claude succeeds."""
    (tmp_path / "hello.py").write_text("x = 1\n", encoding="utf-8")
    arch_out = _make_arch_out([_make_task("low")])

    for provider in ("gemini", "openrouter"):
        cb = MagicMock()
        cb.call.side_effect = CircuitBreakerOpenError(
            provider=provider,
            state=CircuitBreakerState.OPEN,
            retry_after=999_999.0,
        )
        monkeypatch.setattr(f"orchestrator.agents.executor.providers._cb_{provider}", cb)

    cb_claude = MagicMock()
    cb_claude.call.return_value = ("x = 2\n", 10, 20)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_claude", cb_claude)

    output, _ = _run(tmp_path, arch_out)

    assert len(output.applied) == 1, f"errors={output.errors}"
    assert output.errors == []


# ---------------------------------------------------------------------------
# Test 3 — Low risk: Gemini ClientError(403) → OpenRouter HTTPStatusError(403) →
#           Claude succeeds (non-CB exceptions trigger fallback)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_low_recoverable_sdk_exception(monkeypatch, tmp_path):
    """
    When a provider raises a non-CB recoverable exception (e.g. HTTP 403),
    the chain must fall through to the next provider.
    """
    import httpx
    from google.genai import errors as gemini_errors

    (tmp_path / "hello.py").write_text("x = 1\n", encoding="utf-8")
    arch_out = _make_arch_out([_make_task("low")])

    # Mock _call_gemini to raise a google APIError (403 equivalent)
    cb_gemini = MagicMock()
    cb_gemini.call.side_effect = gemini_errors.APIError(403, response_json={})
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini)

    # Mock _call_openrouter to raise httpx HTTPStatusError (403)
    cb_openrouter = MagicMock()
    cb_openrouter.call.side_effect = httpx.HTTPStatusError(
        "403 Forbidden",
        request=MagicMock(),
        response=MagicMock(status_code=403),
    )
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_openrouter", cb_openrouter)

    # Mock _call_claude succeeds
    cb_claude = MagicMock()
    cb_claude.call.return_value = ("x = 2\n", 10, 20)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_claude", cb_claude)

    output, _ = _run(tmp_path, arch_out)

    assert len(output.applied) == 1, f"errors={output.errors}"
    assert output.errors == []


# ---------------------------------------------------------------------------
# Test 4 — Medium risk: OpenRouter CB open → Gemini succeeds (1 hop)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_medium_fallback_to_gemini(monkeypatch, tmp_path):
    """OpenRouter CB open → Gemini succeeds."""
    (tmp_path / "hello.py").write_text("x = 1\n", encoding="utf-8")
    arch_out = _make_arch_out([_make_task("medium")])

    cb_openrouter = MagicMock()
    cb_openrouter.call.side_effect = CircuitBreakerOpenError(
        provider="openrouter",
        state=CircuitBreakerState.OPEN,
        retry_after=999_999.0,
    )
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_openrouter", cb_openrouter)

    cb_gemini = MagicMock()
    cb_gemini.call.return_value = ("x = 2\n", 10, 20)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini)

    output, _ = _run(tmp_path, arch_out)

    cb_openrouter.call.assert_called_once()
    cb_gemini.call.assert_called_once()
    assert len(output.applied) == 1, f"errors={output.errors}"


# ---------------------------------------------------------------------------
# Test 5 — Medium risk: OpenRouter returns empty → Gemini succeeds
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_medium_empty_then_gemini(monkeypatch, tmp_path):
    """OpenRouter returns empty string → chain continues to Gemini."""
    (tmp_path / "hello.py").write_text("x = 1\n", encoding="utf-8")
    arch_out = _make_arch_out([_make_task("medium")])

    cb_openrouter = MagicMock()
    cb_openrouter.call.return_value = ("", 0, 0)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_openrouter", cb_openrouter)

    cb_gemini = MagicMock()
    cb_gemini.call.return_value = ("x = 2\n", 10, 20)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini)

    output, _ = _run(tmp_path, arch_out)

    assert len(output.applied) == 1, f"errors={output.errors}"
    assert output.errors == []


# ---------------------------------------------------------------------------
# Test 6 — High risk: Claude CB open → ERROR (no fallback)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_high_fails_no_fallback(monkeypatch, tmp_path):
    """High-risk task must fail when Claude is unavailable."""
    (tmp_path / "hello.py").write_text("x = 1\n", encoding="utf-8")
    arch_out = _make_arch_out([_make_task("high")])

    cb_claude = MagicMock()
    cb_claude.call.side_effect = CircuitBreakerOpenError(
        provider="claude",
        state=CircuitBreakerState.OPEN,
        retry_after=999_999.0,
    )
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_claude", cb_claude)

    output, _ = _run(tmp_path, arch_out)

    assert len(output.errors) == 1
    assert "failed" in output.errors[0].error


# ---------------------------------------------------------------------------
# Test 7 — All providers exhausted for low risk
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_all_providers_exhausted(monkeypatch, tmp_path):
    """When all 3 providers are OPEN, task returns error."""
    (tmp_path / "hello.py").write_text("x = 1\n", encoding="utf-8")
    arch_out = _make_arch_out([_make_task("low")])

    for provider in ("gemini", "openrouter", "claude"):
        cb = MagicMock()
        cb.call.side_effect = CircuitBreakerOpenError(
            provider=provider,
            state=CircuitBreakerState.OPEN,
            retry_after=999_999.0,
        )
        monkeypatch.setattr(f"orchestrator.agents.executor.providers._cb_{provider}", cb)

    output, _ = _run(tmp_path, arch_out)

    assert len(output.errors) == 1
    assert "failed" in output.errors[0].error


# ---------------------------------------------------------------------------
# Test 8 — Validator returns raw stderr when Gemini CB is OPEN
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_validator_uses_raw_stderr_when_cb_open(monkeypatch):
    """
    When _cb_validator raises CircuitBreakerOpenError, _summarize_errors
    must return the raw stderr fallback string — not an LLM summary.
    """
    from orchestrator.agents.validator import _summarize_errors
    from orchestrator.schemas.validator_output import ToolResult

    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-test")

    cb_mock = MagicMock()
    cb_mock.call.side_effect = CircuitBreakerOpenError(
        provider="gemini",
        state=CircuitBreakerState.OPEN,
        retry_after=999_999.0,
    )
    monkeypatch.setattr("orchestrator.agents.validator._cb_validator", cb_mock)

    failed_tool = ToolResult(
        tool="ruff",  # type: ignore[arg-type]
        passed=False,
        return_code=1,
        stderr="E501 line too long at hello.py:42",
        stdout="",
    )

    result = _summarize_errors([failed_tool], run_id="test-run-id")

    cb_mock.call.assert_called_once()
    assert "ruff" in result
    assert "E501" in result
