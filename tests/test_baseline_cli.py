"""Characterization tests for baseline CLI behaviour.

Captures exit codes, structural text output, and generated artifacts
for the deterministic (offline) V1 commands: doctor and scan.

These tests are intentionally broad — they verify that behaviour is
preserved across refactoring phases, not that specific strings match.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from orchestrator.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared helpers (same as test_scan.py / test_doctor.py)
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("repo\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _make_pyproject(path: Path) -> None:
    (path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    )


def _make_full_valid_repo(path: Path) -> None:
    _init_git_repo(path)
    _make_pyproject(path)
    (path / "tests").mkdir()
    (path / "tests" / "__init__.py").write_text("")
    (path / "tests" / "test_example.py").write_text("def test_ok(): pass\n")


@pytest.fixture()
def valid_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_full_valid_repo(repo)
    return repo


@pytest.fixture()
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# ---------------------------------------------------------------------------
# doctor — offline, deterministic
# ---------------------------------------------------------------------------


class TestDoctorBaseline:
    def test_doctor_exit_code_0_when_supported(self, valid_repo: Path):
        def fake_check(cmd):
            return (True, "ok")

        with patch("orchestrator.doctor.check_command_available", fake_check):
            result = runner.invoke(app, ["doctor", str(valid_repo)])

        assert result.exit_code == 0

    def test_doctor_exit_code_1_when_unsupported(self, tmp_path: Path):
        result = runner.invoke(app, ["doctor", str(tmp_path)])
        assert result.exit_code == 1

    def test_doctor_stdout_contains_v1_supported(self, valid_repo: Path):
        def fake_check(cmd):
            return (True, "ok")

        with patch("orchestrator.doctor.check_command_available", fake_check):
            result = runner.invoke(app, ["doctor", str(valid_repo)])

        assert "V1 supported" in result.stdout
        assert "V1" in result.stdout

    def test_doctor_json_output_parses(self, valid_repo: Path):
        def fake_check(cmd):
            return (True, "ok")

        with patch("orchestrator.doctor.check_command_available", fake_check):
            result = runner.invoke(app, ["doctor", str(valid_repo), "--json"])

        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert "v1_supported" in parsed
        assert "checks" in parsed
        assert isinstance(parsed["checks"], list)

    def test_doctor_does_not_write_to_target(self, valid_repo: Path):
        before = set(valid_repo.iterdir())

        def fake_check(cmd):
            return (True, "ok")

        with patch("orchestrator.doctor.check_command_available", fake_check):
            runner.invoke(app, ["doctor", str(valid_repo)])

        after = set(valid_repo.iterdir())
        assert before == after


# ---------------------------------------------------------------------------
# scan — offline, deterministic
# ---------------------------------------------------------------------------


def _mock_which(cmd: str) -> str | None:
    if cmd in ("ruff", "pytest"):
        return f"/usr/bin/{cmd}"
    return None


def _mock_tool_run(args, **kwargs):
    """Handles both the module form (``-m cmd``) and bare PATH form."""
    from unittest.mock import MagicMock

    cmd = args[2] if args and len(args) > 2 and args[1] == "-m" else (args[0] if args else "tool")
    result = MagicMock()
    result.returncode = 0
    result.stdout = f"{cmd} 1.0.0\n"
    result.stderr = ""
    return result


class TestScanBaseline:
    def test_scan_exit_code_0_when_supported(self, valid_repo: Path):
        with (
            patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
            patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
        ):
            result = runner.invoke(app, ["scan", str(valid_repo)])

        assert result.exit_code == 0

    def test_scan_stdout_contains_scanner_header(self, valid_repo: Path):
        with (
            patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
            patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
        ):
            result = runner.invoke(app, ["scan", str(valid_repo)])

        assert "PatchForge Scanner" in result.stdout
        assert "V1" in result.stdout or "Findings" in result.stdout

    def test_scan_creates_findings_artifact(self, valid_repo: Path, workspace_dir: Path):
        with (
            patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
            patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
        ):
            result = runner.invoke(
                app, ["scan", str(valid_repo), "--workspace", str(workspace_dir)]
            )

        assert result.exit_code == 0
        runs_dir = workspace_dir / "runs"
        assert runs_dir.exists()
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1
        findings_path = run_dirs[0] / "findings.json"
        assert findings_path.exists()
        data = json.loads(findings_path.read_text())
        assert "v1_supported" in data

    def test_scan_does_not_touch_target(self, valid_repo: Path, workspace_dir: Path):
        with (
            patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
            patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
        ):
            before = set(valid_repo.iterdir())
            runner.invoke(app, ["scan", str(valid_repo), "--workspace", str(workspace_dir)])
            after = set(valid_repo.iterdir())

        assert before == after

    def test_scan_rejects_workspace_inside_target(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)

        with patch("orchestrator.main.bootstrap_environment"):
            result = runner.invoke(
                app,
                ["scan", str(repo), "--workspace", str(repo / "workspace")],
            )

        assert result.exit_code == 1
        assert "outside the target" in result.stdout.lower()
