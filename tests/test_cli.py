"""Tests for CLI commands."""

import hashlib
import json
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
    assert "patchforge doctor" in result.stdout
    assert "patchforge scan" in result.stdout
    assert "patchforge plan" in result.stdout
    assert "patchforge preview" in result.stdout
    assert "patchforge apply" in result.stdout


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


# ---------------------------------------------------------------------------
# Helpers for apply rollback tests
# ---------------------------------------------------------------------------


def _setup_apply_run(tmp_path):
    """Create a minimal git repo + workspace so apply proceeds past guards."""
    repo = tmp_path / "target"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True, text=True
    )
    (repo / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    ws = tmp_path / "workspace"
    ws.mkdir()
    run_dir = ws / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    patch_content = (
        "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-hello\n+world\n"
    )
    (run_dir / "patch.diff").write_text(patch_content)
    run_meta = {
        "run_id": "test-run",
        "status": "previewed",
        "target_path": str(repo),
        "workspace_path": str(ws),
        "base_commit": head,
        "branch": "main",
        "v1_supported": True,
        "patch_checksum": hashlib.sha256(patch_content.encode()).hexdigest(),
        "schema_version": 1,
        "failure_artifacts": [],
    }
    (run_dir / "run.json").write_text(json.dumps(run_meta))
    return repo, ws, head


# ---------------------------------------------------------------------------
# Apply rollback tests
# ---------------------------------------------------------------------------


def test_apply_rollback_block1(tmp_path, monkeypatch):
    """Block 1: apply_patch fails, rollback fails -> exit 1, FATAL message."""
    from orchestrator.schemas.git import GitCommandResult

    repo, ws, _ = _setup_apply_run(tmp_path)

    monkeypatch.setattr(
        "orchestrator.git.apply_patch",
        lambda *a, **kw: GitCommandResult(return_code=1, stdout="", stderr="apply failed"),
    )
    monkeypatch.setattr(
        "orchestrator.git.force_reset_apply",
        lambda *a, **kw: GitCommandResult(return_code=1, stdout="", stderr="reset failed"),
    )

    with patch("orchestrator.main.bootstrap_environment"):
        result = runner.invoke(
            app,
            ["apply", "test-run", "--workspace", str(ws), "--allow-dirty"],
        )

    assert result.exit_code == 1
    assert "FATAL" in result.stdout


def test_apply_rollback_block2_fail(tmp_path, monkeypatch):
    """Block 2: validation fails, rollback fails -> exit 1, FATAL message."""
    from orchestrator.schemas.git import GitCommandResult

    repo, ws, _ = _setup_apply_run(tmp_path)

    monkeypatch.setattr(
        "orchestrator.git.apply_patch",
        lambda *a, **kw: GitCommandResult(return_code=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        "orchestrator.git.force_reset_apply",
        lambda *a, **kw: GitCommandResult(return_code=1, stdout="", stderr="reset failed"),
    )

    class _MockPostVal:
        overall_passed = False

        def model_dump_json(self, **kwargs):
            return json.dumps({"overall_passed": False})

        def model_dump(self):
            return {"overall_passed": False}

    monkeypatch.setattr(
        "orchestrator.agents.validator.run",
        lambda *a, **kw: (_MockPostVal(), {}),
    )

    with patch("orchestrator.main.bootstrap_environment"):
        result = runner.invoke(
            app,
            ["apply", "test-run", "--workspace", str(ws), "--allow-dirty"],
        )

    assert result.exit_code == 1
    assert "FATAL" in result.stdout


def test_apply_rollback_block2_success(tmp_path, monkeypatch):
    """Block 2: validation fails, rollback succeeds -> exit 1, no FATAL."""
    from orchestrator.schemas.git import GitCommandResult

    repo, ws, _ = _setup_apply_run(tmp_path)

    monkeypatch.setattr(
        "orchestrator.git.apply_patch",
        lambda *a, **kw: GitCommandResult(return_code=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        "orchestrator.git.force_reset_apply",
        lambda *a, **kw: GitCommandResult(return_code=0, stdout="", stderr=""),
    )

    class _MockPostVal:
        overall_passed = False

        def model_dump_json(self, **kwargs):
            return json.dumps({"overall_passed": False})

        def model_dump(self):
            return {"overall_passed": False}

    monkeypatch.setattr(
        "orchestrator.agents.validator.run",
        lambda *a, **kw: (_MockPostVal(), {}),
    )

    with patch("orchestrator.main.bootstrap_environment"):
        result = runner.invoke(
            app,
            ["apply", "test-run", "--workspace", str(ws), "--allow-dirty"],
        )

    assert result.exit_code == 1
    assert "FATAL" not in result.stdout
