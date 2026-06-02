"""Tests for CLI exit code mapping based on pipeline status."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from orchestrator.main import app

runner = CliRunner()


def _invoke(status: str, args: list[str] | None = None):
    mock_result = MagicMock()
    mock_result.status = status
    mock_result.run_id = "test"
    mock = MagicMock()
    mock.return_value = mock_result

    with (
        patch("orchestrator.main.bootstrap_environment"),
        patch("orchestrator.main.TargetConfig.load"),
        patch("orchestrator.main.Pipeline") as mock_pipeline_cls,
    ):
        mock_pipeline_cls.return_value.execute.return_value = mock_result
        result = runner.invoke(app, args or ["run", str(Path.cwd())])
    return result


def test_completed_exit_zero():
    assert _invoke("completed").exit_code == 0


def test_awaiting_review_exit_zero():
    assert _invoke("awaiting_review").exit_code == 0


def test_failed_exit_one():
    assert _invoke("failed").exit_code == 1


def test_validation_failed_exit_one():
    assert _invoke("validation_failed").exit_code == 1


def test_scan_rejects_workspace_inside_target(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)

    with (
        patch("orchestrator.main.bootstrap_environment"),
        patch("orchestrator.main.run_scout"),
    ):
        result = runner.invoke(
            app,
            ["scan", str(repo), "--workspace", str(repo / "workspace")],
        )

    assert result.exit_code == 1
    assert "Workspace path must be outside the target repository" in result.stdout
