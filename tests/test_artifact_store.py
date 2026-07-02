import difflib
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.storage.artifact_store import ArtifactStore, DurabilityLevel, WriteResult
from orchestrator.storage.local_store import LocalArtifactStore
from orchestrator.workspace import WorkspaceManager

# ── LocalArtifactStore tests ──────────────────────────────────────────────


def test_local_store_str_roundtrip(tmp_path: Path):
    store = LocalArtifactStore(tmp_path)
    result = store.write("test/hello.txt", "Hello, world!")
    data = store.read(result.ref)
    assert data == "Hello, world!"


def test_local_store_unicode_str(tmp_path: Path):
    store = LocalArtifactStore(tmp_path)
    content = "héllo wörld ☕\n\u00e9\u2603"
    result = store.write("test/unicode.txt", content)
    data = store.read(result.ref)
    assert data == content


def test_local_store_atomicity(tmp_path: Path):
    store = LocalArtifactStore(tmp_path)
    result = store.write("test/atomic.txt", "content")
    tmp_files = list(tmp_path.rglob("*.tmp"))
    assert len(tmp_files) == 0
    assert Path(result.ref).read_text(encoding="utf-8") == "content"


def test_local_store_subdirs_created(tmp_path: Path):
    store = LocalArtifactStore(tmp_path)
    result = store.write("a/b/c/deep.txt", "nested")
    assert Path(result.ref).exists()
    assert Path(result.ref).read_text(encoding="utf-8") == "nested"


def test_local_store_delete(tmp_path: Path):
    store = LocalArtifactStore(tmp_path)
    result = store.write("test/to-delete.txt", "bye")
    assert Path(result.ref).exists()
    store.delete(result.ref)
    assert not Path(result.ref).exists()


def test_local_store_delete_missing_ok(tmp_path: Path):
    store = LocalArtifactStore(tmp_path)
    store.delete(str(tmp_path / "nonexistent.txt"))


def test_local_store_absolute_ref(tmp_path: Path):
    store = LocalArtifactStore(tmp_path)
    path = tmp_path / "legacy.txt"
    path.write_text("legacy", encoding="utf-8")
    data = store.read(str(path))
    assert data == "legacy"


def test_local_store_write_path_traversal(tmp_path: Path):
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ValueError, match="resolves outside store base"):
        store.write("../../evil.txt", "payload")
    escaped = tmp_path.parent.parent / "evil.txt"
    assert not escaped.exists()


def test_local_store_read_path_traversal(tmp_path: Path):
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ValueError, match="resolves outside store base"):
        store.read("../../etc/passwd")


def test_local_store_delete_path_traversal(tmp_path: Path):
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ValueError, match="resolves outside store base"):
        store.delete("../../important.txt")


def test_local_store_read_absolute_outside_base(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    store = LocalArtifactStore(tmp_path)
    try:
        with pytest.raises(ValueError, match="resolves outside store base"):
            store.read(str(outside))
    finally:
        outside.unlink(missing_ok=True)


def test_local_store_delete_absolute_outside_base(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("do not delete", encoding="utf-8")
    store = LocalArtifactStore(tmp_path)
    try:
        with pytest.raises(ValueError, match="resolves outside store base"):
            store.delete(str(outside))
        assert outside.exists()
    finally:
        outside.unlink(missing_ok=True)


def test_local_store_write_result(tmp_path: Path):
    store = LocalArtifactStore(tmp_path)
    result = store.write("test/meta.txt", "data")
    assert isinstance(result.ref, str)
    assert result.ref.startswith(str(tmp_path.resolve()))
    assert result.durability == DurabilityLevel.LOCAL_ATOMIC


# ── CRLF regression tests (Dogfooding 002) ───────────────────────────────


def test_local_store_preserves_lf(tmp_path: Path):
    """Regression: CRLF corruption on Windows (Dogfooding 002)."""
    store = LocalArtifactStore(tmp_path)
    content = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n"
    result = store.write("patch.diff", content)
    raw_bytes = Path(result.ref).read_bytes()
    assert b"\r\n" not in raw_bytes
    assert raw_bytes == content.encode("utf-8")


def test_local_store_lf_idempotency_git_apply(tmp_path: Path):
    """patch.diff written by LocalArtifactStore must pass git apply --check."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    source = repo / "file.py"
    source.write_text("old\n", encoding="utf-8", newline="")
    subprocess.run(["git", "add", "file.py"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    original = "old\n"
    modified = "new\n"
    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile="a/file.py",
            tofile="b/file.py",
        )
    )
    diff_content = "".join(diff_lines)

    store = LocalArtifactStore(tmp_path / "store")
    result = store.write("patch.diff", diff_content)

    proc = subprocess.run(
        ["git", "apply", "--check", result.ref],
        cwd=str(repo),
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode()


# ── ABC contract tests ────────────────────────────────────────────────────


def test_abc_cannot_be_instantiated():
    with pytest.raises(TypeError):
        ArtifactStore()  # type: ignore[abstract]


def test_write_result_is_pydantic():
    wr = WriteResult(ref="some/path", durability=DurabilityLevel.LOCAL_ATOMIC)
    assert wr.model_dump_json(indent=2)


# ── WorkspaceManager delegation tests ─────────────────────────────────────


@pytest.fixture
def workspace_mgr(tmp_path: Path) -> WorkspaceManager:
    mgr = WorkspaceManager(tmp_path)
    mgr.setup()
    return mgr


def _create_run(workspace_mgr: WorkspaceManager, run_id: str):
    workspace_mgr.create_run_directory(run_id)
    meta = RunMetadata(
        run_id=run_id,
        target_path="/dummy",
        workspace_path=str(workspace_mgr.root),
        base_commit="abc",
        branch="main",
        v1_supported=True,
    )
    workspace_mgr.write_run_json(run_id, meta)


def test_write_artifact_still_validates(workspace_mgr: WorkspaceManager):
    run_id = "test_run_001"
    workspace_mgr.create_run_directory(run_id)
    _create_run(workspace_mgr, run_id)

    with pytest.raises(ValueError):
        workspace_mgr.write_artifact(run_id, "../../evil.txt", "")

    with pytest.raises(ValueError):
        workspace_mgr._write_artifact_unchecked(run_id, "../../evil.txt", "")

    with pytest.raises(ValueError):
        workspace_mgr.read_artifact(run_id, "../../evil.txt")


def test_write_artifact_ensure_run_exists(workspace_mgr: WorkspaceManager):
    with pytest.raises(FileNotFoundError):
        workspace_mgr.write_artifact("nonexistent", "test.txt", "data")


def test_write_artifact_returns_ref_string(workspace_mgr: WorkspaceManager):
    run_id = "test_run_002"
    _create_run(workspace_mgr, run_id)
    ref = workspace_mgr.write_artifact(run_id, "test.txt", "hello")
    assert isinstance(ref, str)
    assert ref != ""


def test_workspace_delegates_to_store(tmp_path: Path):
    mock_store = MagicMock(spec=ArtifactStore)
    mock_store.write.return_value = WriteResult(
        ref=str(tmp_path / "runs" / "r_1" / "test.txt"),
        durability=DurabilityLevel.LOCAL_ATOMIC,
    )
    mock_store.read.return_value = "mocked"

    mgr = WorkspaceManager(tmp_path, store=mock_store)
    mgr.setup()

    mgr.create_run_directory("r_1")
    meta = RunMetadata(
        run_id="r_1",
        target_path="/dummy",
        workspace_path=str(mgr.root),
        base_commit="abc",
        branch="main",
        v1_supported=True,
    )
    mgr.write_run_json("r_1", meta)

    ref = mgr.write_artifact("r_1", "artifact.txt", "data")
    mock_store.write.assert_any_call("r_1/artifact.txt", "data")
    assert ref == mock_store.write.return_value.ref

    content = mgr.read_artifact("r_1", "artifact.txt")
    mock_store.read.assert_called_once_with("r_1/artifact.txt")
    assert content == "mocked"


def test_workspace_default_store(tmp_path: Path):
    mgr = WorkspaceManager(tmp_path)
    assert isinstance(mgr.store, LocalArtifactStore)
    assert mgr.store._base == (tmp_path.resolve() / "runs")


def test_dual_write_store_failure(tmp_path: Path):
    mock_store = MagicMock(spec=ArtifactStore)
    mock_store.write.side_effect = RuntimeError("store down")

    mgr = WorkspaceManager(tmp_path, store=mock_store)
    mgr.setup()

    mgr.create_run_directory("r_1")
    meta = RunMetadata(
        run_id="r_1",
        target_path="/dummy",
        workspace_path=str(mgr.root),
        base_commit="abc",
        branch="main",
        v1_supported=True,
    )

    mgr.write_run_json("r_1", meta)

    local_path = mgr.run_dir("r_1") / "run.json"
    assert local_path.exists()

    run_dir = mgr.run_dir("r_1")
    run_json = run_dir / "run.json"
    assert run_json.read_text(encoding="utf-8")


def test_apply_json_final_success_reaches_store(tmp_path: Path):
    mock_store = MagicMock(spec=ArtifactStore)
    mgr = WorkspaceManager(tmp_path, store=mock_store)
    mgr.setup()

    mgr.create_run_directory("r_1")
    meta = RunMetadata(
        run_id="r_1",
        target_path="/dummy",
        workspace_path=str(mgr.root),
        base_commit="abc",
        branch="main",
        v1_supported=True,
    )
    mgr.write_run_json("r_1", meta)

    apply_data = '{"status": "committed_local", "success": true}'
    mgr.write_artifact("r_1", "apply.json", apply_data)

    mock_store.write.assert_any_call("r_1/apply.json", apply_data)


def test_read_artifact_roundtrip(workspace_mgr: WorkspaceManager):
    run_id = "test_rt_001"
    _create_run(workspace_mgr, run_id)
    original = "test content"
    workspace_mgr.write_artifact(run_id, "readtest.txt", original)
    retrieved = workspace_mgr.read_artifact(run_id, "readtest.txt")
    assert retrieved == original
