from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.workspace import WorkspaceManager


@pytest.fixture
def workspace_mgr(tmp_path: Path) -> WorkspaceManager:
    mgr = WorkspaceManager(tmp_path)
    mgr.setup()
    return mgr


def test_create_run_directory(workspace_mgr: WorkspaceManager):
    run_id = "run_20260603_120000_123456"
    run_path = workspace_mgr.create_run_directory(run_id)
    assert run_path.exists()
    assert run_path.is_dir()
    assert run_path == workspace_mgr.root / "runs" / run_id


def test_ensure_run_exists(workspace_mgr: WorkspaceManager):
    run_id = "run_20260603_120000_123456"
    with pytest.raises(FileNotFoundError):
        workspace_mgr.ensure_run_exists(run_id)

    workspace_mgr.create_run_directory(run_id)
    meta = RunMetadata(
        run_id=run_id,
        target_path="/dummy/target",
        workspace_path=str(workspace_mgr.root),
        base_commit="abc",
        branch="main",
        v1_supported=True,
    )
    workspace_mgr.write_run_json(run_id, meta)
    # Should not raise error now
    workspace_mgr.ensure_run_exists(run_id)


def test_read_write_artifact(workspace_mgr: WorkspaceManager):
    run_id = "run_20260603_120000_123456"
    filename = "patch.diff"
    content = "diff --git a/README.md b/README.md\n..."

    workspace_mgr.create_run_directory(run_id)
    meta = RunMetadata(
        run_id=run_id,
        target_path="/dummy/target",
        workspace_path=str(workspace_mgr.root),
        base_commit="abc",
        branch="main",
        v1_supported=True,
    )
    workspace_mgr.write_run_json(run_id, meta)
    workspace_mgr.write_artifact(run_id, filename, content)
    read_content = workspace_mgr.read_artifact(run_id, filename)
    assert read_content == content


def test_read_write_run_json(workspace_mgr: WorkspaceManager):
    run_id = "run_20260603_120000_123456"
    meta = RunMetadata(
        run_id=run_id,
        target_path="/dummy/target",
        workspace_path=str(workspace_mgr.root),
        base_commit="abc",
        branch="main",
        status="scanning",
        created_at=datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc),
        v1_supported=True,
    )

    workspace_mgr.create_run_directory(run_id)
    workspace_mgr.write_run_json(run_id, meta)
    loaded = workspace_mgr.read_run_json(run_id)

    assert loaded.run_id == meta.run_id
    assert loaded.target_path == meta.target_path
    assert loaded.base_commit == meta.base_commit
    assert loaded.status == meta.status
    assert loaded.created_at == meta.created_at
