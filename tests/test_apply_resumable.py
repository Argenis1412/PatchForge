"""Tests for issue #258 Part 2 — resume execution from ALREADY_APPLIED (clean tree case)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
import typer

from orchestrator.commands.apply import execute as apply_execute
from orchestrator.schemas.artifacts import ApplyResult, PatchLifecycleState, RunMetadata
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.validator_output import ValidatorOutput
from orchestrator.workspace import WorkspaceManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(repo_dir: Path) -> tuple[str, str]:
    """Init a repo with one commit. Returns (commit_sha, branch_name)."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=repo_dir)
    _git("config", "user.email", "test@example.com", cwd=repo_dir)
    _git("config", "user.name", "Test User", cwd=repo_dir)
    (repo_dir / "file.txt").write_text("v1", encoding="utf-8")
    _git("add", "-A", cwd=repo_dir)
    _git("commit", "-m", "v1", cwd=repo_dir)
    commit = _git("rev-parse", "HEAD", cwd=repo_dir).stdout.strip()
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_dir).stdout.strip()
    return commit, branch


def _setup_resumable_run(
    tmp_path: Path,
    *,
    checkout_apply_branch: bool = True,
    wal_status: str = "applying",
    wal_pre_apply_head: Optional[str] = None,
    write_backup_file: bool = True,
    write_apply_json: bool = True,
    write_config_snapshot: bool = True,
    config_overrides: Optional[dict] = None,
    dirty_tree: bool = False,
) -> dict:
    """Build a full run directory representing an interrupted apply that
    left the patch applied (ALREADY_APPLIED-eligible), i.e. everything the
    resume path expects to find on disk. Returns a dict of useful handles.
    """
    repo_dir = tmp_path / "repo"
    commit1, original_branch = _init_repo(repo_dir)

    run_id = "test-resume-run"
    branch_name = f"patchforge/{run_id}"

    if checkout_apply_branch:
        _git("checkout", "-b", branch_name, cwd=repo_dir)

    if dirty_tree:
        (repo_dir / "file.txt").write_text("v1-with-uncommitted-patch", encoding="utf-8")

    workspace_path = tmp_path / "workspace"
    workspace = WorkspaceManager(workspace_path)
    workspace.setup()
    run_dir = workspace.create_run_directory(run_id)

    patch_content = "dummy patch content\n"
    checksum = hashlib.sha256(patch_content.encode("utf-8")).hexdigest()
    (run_dir / "patch.diff").write_text(patch_content, encoding="utf-8")

    meta = RunMetadata(
        run_id=run_id,
        target_path=str(repo_dir),
        workspace_path=str(workspace_path),
        base_commit=commit1,
        branch=original_branch,
        status="previewed",
        v1_supported=True,
        patch_checksum=checksum,
    )
    workspace.write_run_json(run_id, meta)

    backup_path = run_dir / "patch.apply-backup.diff"
    if write_backup_file:
        backup_path.write_text(patch_content, encoding="utf-8")

    if write_apply_json:
        apply_result = ApplyResult(
            run_id=run_id,
            applied_at=meta.created_at,
            branch=branch_name,
            success=False,
            status=wal_status,
            pre_apply_head=wal_pre_apply_head or commit1,
            pre_apply_branch=original_branch,
            pre_apply_diff_backup=str(backup_path) if write_backup_file else None,
        )
        (run_dir / "apply.json").write_text(
            apply_result.model_dump_json(indent=2), encoding="utf-8"
        )

    if write_config_snapshot:
        config_kwargs = {"target_path": repo_dir, "workspace_path": workspace_path}
        config_kwargs.update(config_overrides or {})
        config = TargetConfig(**config_kwargs)
        (run_dir / "target_config_snapshot.json").write_text(
            config.model_dump_json(indent=2), encoding="utf-8"
        )

    return {
        "repo_dir": repo_dir,
        "workspace_path": workspace_path,
        "workspace": workspace,
        "run_id": run_id,
        "run_dir": run_dir,
        "branch_name": branch_name,
        "original_branch": original_branch,
        "commit1": commit1,
        "backup_path": backup_path,
    }


def _passing_validator():
    return (ValidatorOutput(overall_passed=True), {})


def _failing_validator():
    return (ValidatorOutput(overall_passed=False), {})


# ---------------------------------------------------------------------------
# Resume success / bypass is_clean
# ---------------------------------------------------------------------------


def test_resume_success(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(tmp_path)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch(
            "orchestrator.agents.validator.run",
            return_value=_passing_validator(),
        ),
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    assert run_metadata.status == "applied"
    assert run_metadata.apply_status == "success"

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["status"] == "applied"
    assert apply_json["success"] is True


def test_resume_bypasses_is_clean(tmp_path: Path) -> None:
    """Regression test for Attack A: a dirty tree (the applied-but-uncommitted
    patch IS the dirt) must not block the ALREADY_APPLIED resume path."""
    ctx = _setup_resumable_run(tmp_path, dirty_tree=True)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch(
            "orchestrator.agents.validator.run",
            return_value=_passing_validator(),
        ),
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    assert run_metadata.status == "applied"


# ---------------------------------------------------------------------------
# Validator failure / exception -> rollback
# ---------------------------------------------------------------------------


def test_resume_validator_failure_rollback(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(tmp_path)
    rollback_mock = MagicMock(return_value=None)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", return_value=_failing_validator()),
        patch("orchestrator.agents.executor.rollback_to_commit", rollback_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    rollback_mock.assert_called_once_with(
        ctx["repo_dir"], ctx["commit1"], backup_diff=ctx["backup_path"]
    )

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["status"] == "apply_failed"
    assert apply_json["rolled_back"] is True

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    assert run_metadata.status == "failed"


def test_resume_validator_exception_rollback(tmp_path: Path) -> None:
    """Critical: a validator that crashes must never be treated as an
    implicit pass -- it must route through the same rollback path as an
    explicit validation failure."""
    ctx = _setup_resumable_run(tmp_path)
    rollback_mock = MagicMock(return_value=None)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch(
            "orchestrator.agents.validator.run",
            side_effect=RuntimeError("validator crashed"),
        ),
        patch("orchestrator.agents.executor.rollback_to_commit", rollback_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    rollback_mock.assert_called_once()

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["status"] == "apply_failed"
    assert apply_json["rolled_back"] is True

    failure_detail = json.loads(
        (ctx["run_dir"] / "post_apply_failure.json").read_text(encoding="utf-8")
    )
    assert failure_detail["reason"] == "validator_errored"


# ---------------------------------------------------------------------------
# WAL not hydratable / backup missing -> abort, no validation attempted
# ---------------------------------------------------------------------------


def test_resume_aborts_wal_missing(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(tmp_path, write_apply_json=False)
    validator_mock = MagicMock(return_value=_passing_validator())

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()


def test_resume_aborts_wal_wrong_status(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(tmp_path, wal_status="applied")
    validator_mock = MagicMock(return_value=_passing_validator())

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()


def test_resume_aborts_wal_backup_missing(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(tmp_path, write_backup_file=False)
    validator_mock = MagicMock(return_value=_passing_validator())

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Isolation checks: branch / HEAD mismatch -> abort
# ---------------------------------------------------------------------------


def test_resume_aborts_branch_mismatch(tmp_path: Path) -> None:
    """Regression test: the WAL's recorded branch must be compared against
    the LIVE current branch, not a locally re-derived constant (which would
    always match itself and never catch the user having switched branches)."""
    ctx = _setup_resumable_run(tmp_path, checkout_apply_branch=False)
    validator_mock = MagicMock(return_value=_passing_validator())

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()


def test_resume_aborts_head_mismatch(tmp_path: Path) -> None:
    bogus_sha = "0" * 40
    ctx = _setup_resumable_run(tmp_path, wal_pre_apply_head=bogus_sha)
    validator_mock = MagicMock(return_value=_passing_validator())

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Config snapshot: required on resume, never reloaded from the mutated tree
# ---------------------------------------------------------------------------


def test_resume_aborts_config_snapshot_missing(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(tmp_path, write_config_snapshot=False)
    validator_mock = MagicMock(return_value=_passing_validator())

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()


def test_resume_uses_config_snapshot(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(
        tmp_path, config_overrides={"lint_command": ["echo", "snapshot-marker"]}
    )
    captured_config = {}

    def _fake_validator_run(config=None, **kwargs):
        captured_config["config"] = config
        return _passing_validator()

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", side_effect=_fake_validator_run),
        patch(
            "orchestrator.schemas.config.TargetConfig.load",
            return_value=TargetConfig(
                target_path=ctx["repo_dir"],
                workspace_path=ctx["workspace_path"],
                lint_command=["echo", "load-marker"],
            ),
        ),
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert captured_config["config"].lint_command == ["echo", "snapshot-marker"]


def test_resume_never_calls_target_config_load(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(tmp_path)
    load_mock = MagicMock(side_effect=AssertionError("TargetConfig.load must not be called"))

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", return_value=_passing_validator()),
        patch("orchestrator.schemas.config.TargetConfig.load", load_mock),
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    load_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path: config snapshot written before WAL checkpoint 1
# ---------------------------------------------------------------------------


def test_config_snapshot_written_happy_path(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(
        tmp_path, checkout_apply_branch=False, write_apply_json=False, write_config_snapshot=False
    )
    apply_patch_mock = MagicMock(return_value=MagicMock(return_code=0, stderr=""))

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.git.apply_patch", apply_patch_mock),
        patch("orchestrator.agents.validator.run", return_value=_passing_validator()),
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    snapshot_path = ctx["run_dir"] / "target_config_snapshot.json"
    assert snapshot_path.exists()
    snapshot = TargetConfig.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot.target_path == ctx["repo_dir"].resolve()

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    assert run_metadata.status == "applied"


# ---------------------------------------------------------------------------
# Lock contention -> abort immediately, before lifecycle classification
# ---------------------------------------------------------------------------


def test_resume_aborts_lock_contention(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(tmp_path)
    lifecycle_mock = MagicMock(return_value=PatchLifecycleState.ALREADY_APPLIED)
    coordination_db_dir = tmp_path / "coordination"
    coordination_db_dir.mkdir()

    with (
        patch("orchestrator.commands.apply.acquire_repo_lock", return_value=False),
        patch("orchestrator.lifecycle.classify_lifecycle", lifecycle_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(
            ctx["run_id"],
            workspace=ctx["workspace_path"],
            worker_id="worker-1",
            coordination_db_dir=coordination_db_dir,
        )

    assert exc.value.exit_code == 1
    lifecycle_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Diff-review follow-ups: gaps flagged in the Part 2 diff review
# ---------------------------------------------------------------------------


def test_valid_dirty_tree_blocks_without_allow_dirty(tmp_path: Path) -> None:
    """Regression test for the is_clean relocation itself: moving the check
    into the VALID-only branch must not have inverted or dropped it. A dirty
    tree on the VALID (non-resume) path must still abort before validation,
    exactly like before the refactor."""
    ctx = _setup_resumable_run(
        tmp_path,
        checkout_apply_branch=False,
        dirty_tree=True,
        write_apply_json=False,
        write_config_snapshot=False,
    )
    validator_mock = MagicMock(return_value=_passing_validator())

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    # Reached far enough to record lifecycle_state (classify_lifecycle ran),
    # but must not have reached "applied" / "applying" -- the is_clean gate
    # must have stopped it before any WAL checkpoint.
    assert run_metadata.status == "previewed"


def test_resume_aborts_wal_backup_pointer_stale(tmp_path: Path) -> None:
    """Distinct from test_resume_aborts_wal_backup_missing: here the WAL
    records a non-empty backup path, but the file itself was deleted after
    the WAL was written (e.g. cleaned up by another process). This exercises
    the second, independent guard in _hydrate_apply_result_for_resume."""
    ctx = _setup_resumable_run(tmp_path)
    ctx["backup_path"].unlink()
    validator_mock = MagicMock(return_value=_passing_validator())

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()


def test_resume_aborts_corrupt_apply_json(tmp_path: Path) -> None:
    """apply.json exists but is not valid JSON / does not match ApplyResult
    -- must be treated the same as a missing WAL, not raise unhandled."""
    ctx = _setup_resumable_run(tmp_path, write_apply_json=False)
    (ctx["run_dir"] / "apply.json").write_text("{not valid json", encoding="utf-8")
    validator_mock = MagicMock(return_value=_passing_validator())

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()


def test_resume_aborts_corrupt_config_snapshot(tmp_path: Path) -> None:
    """target_config_snapshot.json exists but is not valid JSON / does not
    match TargetConfig -- must abort cleanly, not raise unhandled."""
    ctx = _setup_resumable_run(tmp_path, write_config_snapshot=False)
    (ctx["run_dir"] / "target_config_snapshot.json").write_text("{not valid json", encoding="utf-8")
    validator_mock = MagicMock(return_value=_passing_validator())

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()


def test_lock_released_on_conflict(tmp_path: Path) -> None:
    """The repo lock is acquired before lifecycle classification now, so it
    must still be released via the `finally` block when classification
    yields a state (CONFLICT/STALE/REBASEABLE) that aborts before reaching
    the resume/happy-path split."""
    ctx = _setup_resumable_run(tmp_path)
    release_mock = MagicMock()
    coordination_db_dir = tmp_path / "coordination"
    coordination_db_dir.mkdir()

    with (
        patch("orchestrator.commands.apply.acquire_repo_lock", return_value=True),
        patch("orchestrator.commands.apply.release_repo_lock", release_mock),
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.CONFLICT,
        ),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(
            ctx["run_id"],
            workspace=ctx["workspace_path"],
            worker_id="worker-1",
            coordination_db_dir=coordination_db_dir,
        )

    assert exc.value.exit_code == 1
    release_mock.assert_called_once()
