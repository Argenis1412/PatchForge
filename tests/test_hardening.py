import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import typer

from orchestrator.commands.apply import execute as apply_execute
from orchestrator.git import resolve_dirt_ref
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


def test_apply_aborts_if_head_changed(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
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

    # issue #270: the early "HEAD has changed" check was removed;
    # classify_lifecycle() now dispatches this case. The dummy patch
    # content doesn't apply against the diverged HEAD, so this classifies
    # as CONFLICT (not REBASEABLE) -- neither branch writes failure.json,
    # unlike the old early-exit.
    captured = capsys.readouterr()
    assert "CONFLICT" in captured.out
    assert "diverged from base commit" in captured.out
    failure_json = run_dir / "failure.json"
    assert not failure_json.exists()


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _setup_rebaseable_scenario(tmp_path: Path, run_id: str) -> dict:
    """Build a repo where HEAD has diverged from base_commit but the patch
    still applies cleanly -- classify_lifecycle() should return REBASEABLE.

    commit1 (base_commit) only has file.txt="v1". The patch adds a new,
    unrelated file (patched.txt) relative to commit1. commit2 then modifies
    file.txt only -- HEAD moves past base_commit, but since the patch never
    touches file.txt, `git apply --check` still passes against commit2.
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _git("init", cwd=repo_dir)
    _git("config", "user.email", "test@example.com", cwd=repo_dir)
    _git("config", "user.name", "Test User", cwd=repo_dir)
    (repo_dir / "file.txt").write_text("v1", encoding="utf-8")
    _git("add", "-A", cwd=repo_dir)
    _git("commit", "-m", "v1", cwd=repo_dir)
    commit1 = _git("rev-parse", "HEAD", cwd=repo_dir).stdout.strip()
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_dir).stdout.strip()

    # Patch: add patched.txt (unrelated to file.txt), relative to commit1.
    (repo_dir / "patched.txt").write_text("patched content\n", encoding="utf-8")
    _git("add", "patched.txt", cwd=repo_dir)
    patch_content = _git("diff", "--cached", cwd=repo_dir).stdout
    _git("reset", cwd=repo_dir)
    (repo_dir / "patched.txt").unlink()

    # commit2: diverges HEAD from commit1, but only touches file.txt.
    (repo_dir / "file.txt").write_text("v2", encoding="utf-8")
    _git("add", "-A", cwd=repo_dir)
    _git("commit", "-m", "v2", cwd=repo_dir)

    workspace_path = tmp_path / "workspace"
    workspace = WorkspaceManager(workspace_path)
    workspace.setup()
    run_dir = workspace.create_run_directory(run_id)

    patch_path = run_dir / "patch.diff"
    # newline="" preserves LF line endings from git diff -- write_text's
    # default universal-newline translation on Windows would rewrite them
    # to CRLF and break "git apply --check".
    patch_path.write_text(patch_content, encoding="utf-8", newline="")
    checksum = hashlib.sha256(patch_content.encode("utf-8")).hexdigest()

    meta = RunMetadata(
        run_id=run_id,
        target_path=str(repo_dir),
        workspace_path=str(workspace_path),
        base_commit=commit1,
        branch=branch,
        status="previewed",
        v1_supported=True,
        patch_checksum=checksum,
    )
    workspace.write_run_json(run_id, meta)

    return {
        "repo_dir": repo_dir,
        "workspace_path": workspace_path,
        "run_dir": run_dir,
        "run_id": run_id,
    }


def test_apply_head_diverged_but_patch_still_applies_yields_rebaseable(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """issue #270: with the early "HEAD has changed" exit removed,
    classify_lifecycle() is the only dispatcher for HEAD-divergence. When
    the patch still applies cleanly against the diverged HEAD, this must
    now report REBASEABLE (not the old generic message), and persist that
    state to run.json. Exercises apply_execute() end-to-end without mocking
    classify_lifecycle -- test_v1_commands.py::test_rebaseable_blocks_apply
    mocks classify_lifecycle directly and would pass identically whether or
    not this fix existed."""
    ctx = _setup_rebaseable_scenario(tmp_path, "test-rebaseable-run")

    with pytest.raises(typer.Exit) as exc:
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1

    captured = capsys.readouterr()
    assert "REBASEABLE" in captured.out

    run_json_path = ctx["run_dir"] / "run.json"
    run_data = json.loads(run_json_path.read_text(encoding="utf-8"))
    assert run_data["lifecycle_state"] == "REBASEABLE"


@pytest.mark.parametrize("allow_dirty", [True, False])
def test_rebaseable_with_dirty_tree_leaves_dirt_untouched(
    tmp_path: Path, allow_dirty: bool
) -> None:
    """issue #270 (adversarial finding): REBASEABLE aborts (apply.py's
    STALE/CONFLICT/REBASEABLE gate) before the code ever reaches the
    `if not git_state.is_clean: if not allow_dirty: ...` block -- that
    block lives only in the happy-path `else`, reached solely when none of
    STALE/CONFLICT/REBASEABLE fired. So `allow_dirty` is never evaluated on
    this path, and an uncommitted change in an unrelated file must survive
    the abort untouched and uncaptured, regardless of the flag's value --
    proving the equivalence instead of assuming it."""
    run_id = f"test-rebaseable-dirty-{allow_dirty}"
    ctx = _setup_rebaseable_scenario(tmp_path, run_id)
    repo_dir = ctx["repo_dir"]

    dirty_file = repo_dir / "dirty.txt"
    dirty_file.write_text("uncommitted dirt\n", encoding="utf-8")

    with pytest.raises(typer.Exit) as exc:
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"], allow_dirty=allow_dirty)

    assert exc.value.exit_code == 1
    assert dirty_file.read_text(encoding="utf-8") == "uncommitted dirt\n"
    assert resolve_dirt_ref(repo_dir, run_id) is None


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


def test_apply_captures_approved_by_at_human_gate(tmp_path: Path) -> None:
    """#241: apply.py must record approved_by from local git config the
    moment the user invokes apply — the actual human gate. Reaching
    CONFLICT is enough to prove the field survives to write_run_json()."""
    from unittest.mock import patch as _patch

    from orchestrator.schemas.artifacts import PatchLifecycleState

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Approver"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "approver@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    (repo_dir / "file.txt").write_text("v1", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "v1"], cwd=repo_dir, check=True, capture_output=True)
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
    )
    commit1 = proc.stdout.strip()
    branch_proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    actual_branch = branch_proc.stdout.strip()

    workspace_path = tmp_path / "workspace"
    workspace = WorkspaceManager(workspace_path)
    workspace.setup()
    run_id = "test-approved-by"
    run_dir = workspace.create_run_directory(run_id)
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

    # CONFLICT reaches the line 199 write_run_json but exits before the
    # apply pipeline runs, keeping the test hermetic. classify_lifecycle
    # is imported lazily inside apply.execute(), so patch the source module.
    with (
        _patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.CONFLICT,
        ),
        pytest.raises(typer.Exit),
    ):
        apply_execute(run_id, workspace=workspace_path)

    persisted = workspace.read_run_json(run_id)
    assert persisted.approved_by == "local:Approver <approver@example.com>"
