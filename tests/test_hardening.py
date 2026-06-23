import json
import os
import subprocess
from pathlib import Path

import pytest
import typer

from orchestrator.commands.apply import execute as apply_execute
from orchestrator.observability.events import log_event
from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.workspace import WorkspaceManager


def test_write_atomic_preserves_file_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceManager(tmp_path)
    workspace.setup()
    run_id = "test-run"
    run_dir = workspace.create_run_directory(run_id)

    # Initial valid run.json
    meta = RunMetadata(
        run_id=run_id,
        target_path="/tmp",
        workspace_path="/tmp/workspace",
        base_commit="abc",
        branch="main",
        status="scanned",
        v1_supported=True,
    )
    workspace.write_run_json(run_id, meta)

    original_content = (run_dir / "run.json").read_text(encoding="utf-8")

    # Monkeypatch to raise exception
    def mock_wal_write(*args, **kwargs):
        raise ValueError("Simulated failure")

    import orchestrator.workspace as _ws_module

    monkeypatch.setattr(_ws_module, "_wal_write", mock_wal_write)

    meta_updated = RunMetadata(
        run_id=run_id,
        target_path="/tmp",
        workspace_path="/tmp/workspace",
        base_commit="abc",
        branch="main",
        status="previewed",
        v1_supported=True,
    )
    with pytest.raises(ValueError, match="Simulated failure"):
        workspace.write_run_json(run_id, meta_updated)

    # Check that failure.json was written
    failure_path = run_dir / "failure.json"
    assert failure_path.exists()
    failure_data = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure_data["error"] == "Failed to write run.json"
    assert "traceback" in failure_data

    # Original file is unchanged
    assert (run_dir / "run.json").read_text(encoding="utf-8") == original_content


def test_events_fsync_called_on_append(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fsync_called = []

    def mock_fsync(fd):
        fsync_called.append(fd)

    monkeypatch.setattr(os, "fsync", mock_fsync)

    log_event("trace-1", "run-1", event="test", logs_dir=tmp_path)

    # We expect at least one fsync (for the file). If posix and new, a second for the dir.
    if os.name == "posix":
        assert len(fsync_called) >= 2
    else:
        assert len(fsync_called) == 1

    pipeline_jsonl = tmp_path / "pipeline.jsonl"
    assert pipeline_jsonl.exists()
    assert "test" in pipeline_jsonl.read_text(encoding="utf-8")


def test_apply_aborts_if_head_changed(tmp_path: Path) -> None:
    # Setup temp git repo
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)

    (repo_dir / "file.txt").write_text("v1", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "v1"], cwd=repo_dir, check=True)

    # Get initial commit
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
    )
    commit1 = proc.stdout.strip()

    # Second commit to change HEAD
    (repo_dir / "file.txt").write_text("v2", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "v2"], cwd=repo_dir, check=True)

    # Setup workspace
    workspace_path = tmp_path / "workspace"
    workspace = WorkspaceManager(workspace_path)
    workspace.setup()
    run_id = "test-run"
    run_dir = workspace.create_run_directory(run_id)

    # Get the actual branch name initialized by git
    branch_proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    actual_branch = branch_proc.stdout.strip()

    meta = RunMetadata(
        run_id=run_id,
        target_path=str(repo_dir),
        workspace_path=str(workspace_path),
        base_commit=commit1,
        branch=actual_branch,
        status="previewed",
        v1_supported=True,
        patch_checksum="dummy",
    )
    workspace.write_run_json(run_id, meta)

    (run_dir / "patch.diff").write_text("dummy patch", encoding="utf-8")

    with pytest.raises(typer.Exit) as exc:
        apply_execute(run_id, workspace=workspace_path)

    assert exc.value.exit_code == 1

    failure_json = run_dir / "failure.json"
    assert failure_json.exists()
    failure_data = json.loads(failure_json.read_text(encoding="utf-8"))
    assert failure_data["error"] == "HEAD has changed"
    assert failure_data["expected"] == commit1


def test_apply_aborts_if_head_resolution_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Setup workspace
    workspace_path = tmp_path / "workspace"
    workspace = WorkspaceManager(workspace_path)
    workspace.setup()
    run_id = "test-run"
    run_dir = workspace.create_run_directory(run_id)

    meta = RunMetadata(
        run_id=run_id,
        target_path="/non-existent-path-invalid-git",
        workspace_path=str(workspace_path),
        base_commit="abc",
        branch="main",
        status="previewed",
        v1_supported=True,
        patch_checksum="dummy",
    )
    workspace.write_run_json(run_id, meta)

    (run_dir / "patch.diff").write_text("dummy patch", encoding="utf-8")

    import orchestrator.git

    def mock_current_head(path):
        raise RuntimeError("Simulated git resolution failure")

    monkeypatch.setattr(orchestrator.git, "current_head", mock_current_head)

    with pytest.raises(typer.Exit) as exc:
        apply_execute(run_id, workspace=workspace_path)

    assert exc.value.exit_code == 1

    failure_json = run_dir / "failure.json"
    assert failure_json.exists()
    failure_data = json.loads(failure_json.read_text(encoding="utf-8"))
    assert failure_data["error"] == "Failed to resolve HEAD"
    assert "Simulated git resolution failure" in failure_data["message"]
