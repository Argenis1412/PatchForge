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


def test_workspace_isolation():
    """Verify WorkspaceManager instances use unique paths based on worker_id."""
    from pathlib import Path

    root = Path("/tmp/test-isolation")
    w1 = WorkspaceManager(root, worker_id="worker-abc")
    w2 = WorkspaceManager(root, worker_id="worker-xyz")

    assert w1.root != w2.root
    assert "worker-abc" in str(w1.root)
    assert "worker-xyz" in str(w2.root)
    assert w1.root.parent == w2.root.parent
    assert w1._worker_id == "worker-abc"
    assert w2._worker_id == "worker-xyz"


def test_stale_workspace_cleanup(tmp_path: Path):
    """Verify cleanup_stale_workspaces removes only old worker directories."""
    import os
    import time

    parent = tmp_path / "workspaces"
    parent.mkdir()

    old_dir = parent / "worker-old"
    old_dir.mkdir()
    old_time = time.time() - 48 * 3600
    os_utime = getattr(os, "utime", None)
    if os_utime:
        os_utime(str(old_dir), (old_time, old_time))

    fresh_dir = parent / "worker-fresh"
    fresh_dir.mkdir()

    non_worker = parent / "not-a-worker"
    non_worker.mkdir()

    mgr = WorkspaceManager(parent / "worker-our")
    mgr.cleanup_stale_workspaces(max_age_hours=24)

    assert not old_dir.exists()
    assert fresh_dir.exists()
    assert non_worker.exists()


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


def test_write_artifact_rejects_traversal(workspace_mgr: WorkspaceManager):
    run_id = "run_20260603_120000_123456"
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
    with pytest.raises(ValueError):
        workspace_mgr.write_artifact(run_id, "../../evil.txt", "")


def test_write_artifact_unchecked_rejects_traversal(workspace_mgr: WorkspaceManager):
    run_id = "run_20260603_120000_123456"
    workspace_mgr.create_run_directory(run_id)
    with pytest.raises(ValueError):
        workspace_mgr._write_artifact_unchecked(run_id, "../../evil.txt", "")


def test_read_artifact_rejects_traversal(workspace_mgr: WorkspaceManager):
    run_id = "run_20260603_120000_123456"
    with pytest.raises(ValueError):
        workspace_mgr.read_artifact(run_id, "../../evil.txt")


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
