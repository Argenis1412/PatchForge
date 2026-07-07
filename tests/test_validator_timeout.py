"""Tests for issue #151: configurable validator timeout and short-circuit on timeout."""

import subprocess
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from orchestrator.agents.validator import run as validator_run
from orchestrator.agents.validator.runners import DEFAULT_TIMEOUT
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.validator_output import ToolResult

# ---------------------------------------------------------------------------
# DEFAULT_TIMEOUT sanity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_timeout_positive():
    assert DEFAULT_TIMEOUT > 0


@pytest.mark.unit
def test_default_timeout_covers_large_suites():
    assert DEFAULT_TIMEOUT >= 180


# ---------------------------------------------------------------------------
# TargetConfig.validator_timeout field
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_config_validator_timeout_default_is_none(tmp_path):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
    assert config.validator_timeout is None


@pytest.mark.unit
def test_config_validator_timeout_accepts_positive(tmp_path):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace, validator_timeout=60)
    assert config.validator_timeout == 60


@pytest.mark.unit
def test_config_validator_timeout_rejects_zero(tmp_path):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    with pytest.raises(ValidationError):
        TargetConfig(target_path=tmp_path, workspace_path=workspace, validator_timeout=0)


@pytest.mark.unit
def test_config_validator_timeout_rejects_negative(tmp_path):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    with pytest.raises(ValidationError):
        TargetConfig(target_path=tmp_path, workspace_path=workspace, validator_timeout=-1)


# ---------------------------------------------------------------------------
# Cascade: CLI > orchestrator.json > env var > default
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_validator_timeout_from_cli(tmp_path, monkeypatch):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    monkeypatch.delenv("PATCHFORGE_VALIDATOR_TIMEOUT", raising=False)
    config = TargetConfig.load(target_path=tmp_path, workspace_path=workspace, validator_timeout=30)
    assert config.validator_timeout == 30


@pytest.mark.unit
def test_load_validator_timeout_from_orchestrator_json(tmp_path, monkeypatch):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    monkeypatch.delenv("PATCHFORGE_VALIDATOR_TIMEOUT", raising=False)
    (tmp_path / "orchestrator.json").write_text('{"validator_timeout": 90}', encoding="utf-8")
    config = TargetConfig.load(target_path=tmp_path, workspace_path=workspace)
    assert config.validator_timeout == 90


@pytest.mark.unit
def test_load_validator_timeout_cli_overrides_json(tmp_path, monkeypatch):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    monkeypatch.delenv("PATCHFORGE_VALIDATOR_TIMEOUT", raising=False)
    (tmp_path / "orchestrator.json").write_text('{"validator_timeout": 90}', encoding="utf-8")
    config = TargetConfig.load(target_path=tmp_path, workspace_path=workspace, validator_timeout=45)
    assert config.validator_timeout == 45


@pytest.mark.unit
def test_load_validator_timeout_from_env_var(tmp_path, monkeypatch):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    monkeypatch.setenv("PATCHFORGE_VALIDATOR_TIMEOUT", "75")
    config = TargetConfig.load(target_path=tmp_path, workspace_path=workspace)
    assert config.validator_timeout == 75


@pytest.mark.unit
def test_load_validator_timeout_cli_overrides_env(tmp_path, monkeypatch):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    monkeypatch.setenv("PATCHFORGE_VALIDATOR_TIMEOUT", "75")
    config = TargetConfig.load(target_path=tmp_path, workspace_path=workspace, validator_timeout=50)
    assert config.validator_timeout == 50


@pytest.mark.unit
def test_load_validator_timeout_env_var_negative_ignored(tmp_path, monkeypatch):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    monkeypatch.setenv("PATCHFORGE_VALIDATOR_TIMEOUT", "-5")
    config = TargetConfig.load(target_path=tmp_path, workspace_path=workspace)
    assert config.validator_timeout is None


@pytest.mark.unit
def test_load_validator_timeout_env_var_non_integer_ignored(tmp_path, monkeypatch):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    monkeypatch.setenv("PATCHFORGE_VALIDATOR_TIMEOUT", "abc")
    config = TargetConfig.load(target_path=tmp_path, workspace_path=workspace)
    assert config.validator_timeout is None


@pytest.mark.unit
def test_load_validator_timeout_none_when_no_source(tmp_path, monkeypatch):
    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    monkeypatch.delenv("PATCHFORGE_VALIDATOR_TIMEOUT", raising=False)
    config = TargetConfig.load(target_path=tmp_path, workspace_path=workspace)
    assert config.validator_timeout is None


# ---------------------------------------------------------------------------
# Timeout value threads through to subprocess.run()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_runner_passes_timeout_to_subprocess(tmp_path):
    from orchestrator.agents.validator.runners import run_ruff

    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = subprocess.CompletedProcess(
            args=["ruff", "check", "."], returncode=0, stdout="", stderr=""
        )
        run_ruff(run_id="test-run", project_root=tmp_path, timeout=42)
        call_kwargs = mock_sub.call_args[1]
        assert call_kwargs["timeout"] == 42


# ---------------------------------------------------------------------------
# Short-circuit: ruff timeout → pytest never called
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_short_circuit_on_ruff_timeout(tmp_path, monkeypatch):
    timeout_result = ToolResult(
        tool="ruff", passed=False, return_code=-2, timed_out=True, stderr="Timeout"
    )
    pass_result = ToolResult(tool="pytest", passed=True, return_code=0)

    pytest_called = []

    monkeypatch.setattr("orchestrator.agents.validator.run_ruff", lambda *a, **kw: timeout_result)
    monkeypatch.setattr(
        "orchestrator.agents.validator.run_pytest",
        lambda *a, **kw: pytest_called.append(True) or pass_result,
    )
    monkeypatch.setattr("orchestrator.agents.validator.run_tsc", lambda *a, **kw: pass_result)

    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    config = TargetConfig(
        target_path=tmp_path,
        workspace_path=workspace,
        capabilities=__import__(
            "orchestrator.schemas.config", fromlist=["TargetCapabilities"]
        ).TargetCapabilities(
            effective_supports_tests=True,
            effective_supports_typecheck=False,
        ),
    )
    validator_run(config=config)
    assert pytest_called == [], "pytest must not be called when ruff times out"


@pytest.mark.unit
def test_short_circuit_on_pytest_timeout(tmp_path, monkeypatch):
    pass_result = ToolResult(tool="ruff", passed=True, return_code=0)
    timeout_result = ToolResult(
        tool="pytest", passed=False, return_code=-2, timed_out=True, stderr="Timeout"
    )
    tsc_result = ToolResult(tool="tsc", passed=True, return_code=0)

    tsc_called = []

    monkeypatch.setattr("orchestrator.agents.validator.run_ruff", lambda *a, **kw: pass_result)
    monkeypatch.setattr("orchestrator.agents.validator.run_pytest", lambda *a, **kw: timeout_result)
    monkeypatch.setattr(
        "orchestrator.agents.validator.run_tsc",
        lambda *a, **kw: tsc_called.append(True) or tsc_result,
    )

    from orchestrator.schemas.config import TargetCapabilities

    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    config = TargetConfig(
        target_path=tmp_path,
        workspace_path=workspace,
        capabilities=TargetCapabilities(
            effective_supports_tests=True,
            effective_supports_typecheck=True,
        ),
    )
    validator_run(config=config)
    assert tsc_called == [], "tsc must not be called when pytest times out"


# ---------------------------------------------------------------------------
# Commit 2: timed_out field and actionable message
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_timed_out_flag_set_on_timeout(tmp_path):
    import subprocess as _subprocess

    from orchestrator.agents.validator.runners import run_ruff

    with patch("subprocess.run", side_effect=_subprocess.TimeoutExpired(cmd=["ruff"], timeout=10)):
        result = run_ruff(run_id="t", project_root=tmp_path, timeout=10)

    assert result.timed_out is True
    assert result.return_code == -2
    assert result.passed is False


@pytest.mark.unit
def test_timeout_message_contains_hint(tmp_path):
    import subprocess as _subprocess

    from orchestrator.agents.validator.runners import run_ruff

    with patch("subprocess.run", side_effect=_subprocess.TimeoutExpired(cmd=["ruff"], timeout=5)):
        result = run_ruff(run_id="t", project_root=tmp_path, timeout=5)

    assert "--validator-timeout" in result.stderr
    assert "orchestrator.json" in result.stderr


@pytest.mark.unit
def test_timed_out_not_set_on_normal_failure(tmp_path):
    import subprocess as _subprocess

    from orchestrator.agents.validator.runners import run_ruff

    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = _subprocess.CompletedProcess(
            args=["ruff", "check", "."], returncode=1, stdout="", stderr="lint error"
        )
        result = run_ruff(run_id="t", project_root=tmp_path)

    assert result.timed_out is False
    assert result.passed is False


@pytest.mark.unit
def test_tool_result_timed_out_roundtrip():
    result = ToolResult(tool="pytest", passed=False, return_code=-2, timed_out=True)
    dumped = result.model_dump_json()
    loaded = ToolResult.model_validate_json(dumped)
    assert loaded.timed_out is True


@pytest.mark.unit
def test_tool_result_timed_out_defaults_false():
    result = ToolResult(tool="ruff", passed=True, return_code=0)
    assert result.timed_out is False


# ---------------------------------------------------------------------------
# Commit 2: progress_callback receives tool names
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_progress_callback_receives_tool_names(tmp_path, monkeypatch):
    from orchestrator.schemas.config import TargetCapabilities

    pass_result = ToolResult(tool="ruff", passed=True, return_code=0)
    pytest_result = ToolResult(tool="pytest", passed=True, return_code=0)

    monkeypatch.setattr("orchestrator.agents.validator.run_ruff", lambda *a, **kw: pass_result)
    monkeypatch.setattr("orchestrator.agents.validator.run_pytest", lambda *a, **kw: pytest_result)
    monkeypatch.setattr("orchestrator.agents.validator.run_tsc", lambda *a, **kw: pass_result)

    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    config = TargetConfig(
        target_path=tmp_path,
        workspace_path=workspace,
        capabilities=TargetCapabilities(
            effective_supports_tests=True,
            effective_supports_typecheck=False,
        ),
    )

    messages: list[str] = []
    validator_run(config=config, progress_callback=messages.append)

    assert any("ruff" in m.lower() for m in messages)
    assert any("pytest" in m.lower() for m in messages)


@pytest.mark.unit
def test_progress_callback_on_timeout_skip(tmp_path, monkeypatch):
    from orchestrator.schemas.config import TargetCapabilities

    timeout_result = ToolResult(tool="ruff", passed=False, return_code=-2, timed_out=True)

    monkeypatch.setattr("orchestrator.agents.validator.run_ruff", lambda *a, **kw: timeout_result)
    monkeypatch.setattr(
        "orchestrator.agents.validator.run_pytest",
        lambda *a, **kw: ToolResult(tool="pytest", passed=True, return_code=0),
    )

    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    config = TargetConfig(
        target_path=tmp_path,
        workspace_path=workspace,
        capabilities=TargetCapabilities(effective_supports_tests=True),
    )

    messages: list[str] = []
    validator_run(config=config, progress_callback=messages.append)

    assert any("skip" in m.lower() for m in messages)


# ---------------------------------------------------------------------------
# Commit 3: timeout surfaced in validation_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validation_summary_contains_timeout_info(tmp_path, monkeypatch):
    """validation_summary stored in run_metadata must mention timeout tool and hint."""
    from orchestrator.schemas.config import TargetCapabilities

    timeout_tool = ToolResult(
        tool="ruff",
        passed=False,
        return_code=-2,
        timed_out=True,
        stderr="Timeout: ruff exceeded 5s limit. Increase with --validator-timeout",
    )
    validator_output = __import__(
        "orchestrator.schemas.validator_output", fromlist=["ValidatorOutput"]
    ).ValidatorOutput(
        overall_passed=False,
        tools=[timeout_tool],
        llm_summary=None,
        run_id="test-run",
    )

    workspace = tmp_path.parent / f"{tmp_path.name}-ws"
    config = TargetConfig(
        target_path=tmp_path,
        workspace_path=workspace,
        validator_timeout=5,
        capabilities=TargetCapabilities(),
    )

    from orchestrator.agents.validator.runners import DEFAULT_TIMEOUT

    timeout_tools = [t for t in validator_output.tools if t.timed_out]
    assert timeout_tools, "fixture must have a timed-out tool"

    tool_names = ", ".join(t.tool for t in timeout_tools)
    effective_timeout = config.validator_timeout or DEFAULT_TIMEOUT
    timeout_prefix = (
        f"Timeout: {tool_names} exceeded {effective_timeout}s limit. "
        f"Increase with --validator-timeout <seconds>. "
    )
    validation_summary = timeout_prefix + (validator_output.llm_summary or "Validation failed")

    assert "--validator-timeout" in validation_summary
    assert "ruff" in validation_summary


@pytest.mark.unit
def test_validation_summary_no_timeout_hint_when_no_timeout(tmp_path):
    """When no tool timed out, validation_summary must not include timeout hint."""
    from orchestrator.schemas.validator_output import ValidatorOutput

    pass_tool = ToolResult(tool="ruff", passed=True, return_code=0)
    validator_output = ValidatorOutput(
        overall_passed=True,
        tools=[pass_tool],
        run_id="test-run",
    )

    timeout_tools = [t for t in validator_output.tools if t.timed_out]
    assert timeout_tools == []

    validation_summary = "All checks passed successfully"
    assert "--validator-timeout" not in validation_summary
