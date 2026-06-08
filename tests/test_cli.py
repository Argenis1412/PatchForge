"""Tests for CLI commands."""

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from orchestrator.main import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip(text: str) -> str:
    return _ANSI_RE.sub("", text)


def test_run_hidden_from_help():
    result = runner.invoke(app, ["--help"])
    clean = _strip(result.stdout)
    assert "│ run " not in clean
    assert "│ doctor" in clean
    assert "│ scan" in clean
    assert "│ plan" in clean
    assert "│ preview" in clean
    assert "│ apply" in clean


def test_run_still_callable():
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 0


def test_run_shows_deprecation_warning():
    result = runner.invoke(app, ["run", str(Path.cwd())])
    assert result.exit_code == 0
    assert "deprecated" in result.stdout
    assert "orchestrator scan" in result.stdout
    assert "orchestrator plan" in result.stdout
    assert "orchestrator preview" in result.stdout
    assert "orchestrator apply" in result.stdout


def test_run_rejects_legacy_flags():
    result = runner.invoke(app, ["run", str(Path.cwd()), "--dry-run"])
    assert result.exit_code != 0
    assert "No such option" in result.stderr


def test_scan_rejects_workspace_inside_target(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)

    with patch("orchestrator.main.bootstrap_environment"):
        result = runner.invoke(
            app,
            ["scan", str(repo), "--workspace", str(repo / "workspace")],
        )

    assert result.exit_code == 1
    assert "Workspace path must be outside the target repository" in result.stdout
