from __future__ import annotations

import sys

from orchestrator.agents.validator.process import (
    build_isolated_environment,
    build_venv_environment,
    execute_process,
    prepare_process,
)
from orchestrator.provider_policy import PROVIDER_ENV_VARS


def test_validator_child_environments_exclude_provider_credentials(tmp_path, monkeypatch):
    for variable in PROVIDER_ENV_VARS.values():
        monkeypatch.setenv(variable, "operator-secret")
    monkeypatch.setenv("UNRELATED_VARIABLE", "preserved")

    venv_bin = tmp_path / ".venv" / "Scripts"
    venv_bin.mkdir(parents=True)

    v1_environment = build_venv_environment(tmp_path)
    v2_environment = build_isolated_environment(tmp_path / "scratch")

    for environment in (v1_environment, v2_environment):
        assert environment["UNRELATED_VARIABLE"] == "preserved"
        assert all(variable not in environment for variable in PROVIDER_ENV_VARS.values())


def test_validator_v1_and_v2_processes_cannot_read_provider_credentials(tmp_path, monkeypatch):
    for variable in PROVIDER_ENV_VARS.values():
        monkeypatch.setenv(variable, "operator-secret")
    command = [
        sys.executable,
        "-c",
        "import os; print(any(os.getenv(k) for k in "
        "('ANTHROPIC_API_KEY','GOOGLE_API_KEY','OPENROUTER_API_KEY')))",
    ]

    for environment in (
        build_venv_environment(tmp_path),
        build_isolated_environment(tmp_path / "scratch"),
    ):
        result = execute_process(
            prepare_process(command, tmp_path, environment=environment), timeout=10
        )
        assert result.return_code == 0
        assert result.stdout.strip() == "False"
