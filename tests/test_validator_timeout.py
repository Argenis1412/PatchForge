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
    timeout_result = ToolResult(tool="ruff", passed=False, return_code=-2, stderr="Timeout")
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
    timeout_result = ToolResult(tool="pytest", passed=False, return_code=-2, stderr="Timeout")
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
