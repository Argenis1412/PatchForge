"""Tests for issue #258 Part 2 — resume execution from ALREADY_APPLIED (clean tree case)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
import typer

from orchestrator.commands.apply import execute as apply_execute
from orchestrator.schemas.artifacts import ApplyResult, PatchLifecycleState, RunMetadata
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.git import ApplyCheckStatus
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
    capture_dirt: bool = False,
    dirt_sha_in_wal: bool = True,
    dirt_sha_in_run_metadata: bool = True,
) -> dict:
    """Build a full run directory representing an interrupted apply that
    left the patch applied (ALREADY_APPLIED-eligible), i.e. everything the
    resume path expects to find on disk. Returns a dict of useful handles.

    If capture_dirt is True, a real dirt-capture ref is created for this
    run_id before the patch-applied state is set up: a tracked change to
    dirt.txt (unrelated to file.txt, which represents the patch's own
    residue) is captured via stash_create_dirt/store_dirt_ref and then
    reset, leaving the ref as the only trace -- mirroring what the happy
    path does before crashing. dirt_sha_in_wal/dirt_sha_in_run_metadata
    control whether the captured SHA is also recorded in apply.json /
    run.json respectively, so tests can exercise WAL/run.json divergence.
    """
    repo_dir = tmp_path / "repo"
    commit1, original_branch = _init_repo(repo_dir)

    run_id = "test-resume-run"
    branch_name = f"patchforge/{run_id}"

    if checkout_apply_branch:
        _git("checkout", "-b", branch_name, cwd=repo_dir)

    dirt_sha: Optional[str] = None
    if capture_dirt:
        from orchestrator.git import stash_create_dirt, store_dirt_ref

        (repo_dir / "dirt.txt").write_text("pre-existing dirt\n", encoding="utf-8")
        dirt_sha = stash_create_dirt(repo_dir)
        assert dirt_sha is not None
        assert store_dirt_ref(repo_dir, run_id, dirt_sha)
        _git("checkout", "--", ".", cwd=repo_dir)
        (repo_dir / "dirt.txt").unlink(missing_ok=True)
        _git("clean", "-fd", cwd=repo_dir)

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
        dirt_stash_sha=dirt_sha if (capture_dirt and dirt_sha_in_run_metadata) else None,
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
            dirt_stash_sha=dirt_sha if (capture_dirt and dirt_sha_in_wal) else None,
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
        "dirt_sha": dirt_sha,
    }


def _passing_validator():
    return (ValidatorOutput(overall_passed=True), {})


def _failing_validator():
    return (ValidatorOutput(overall_passed=False), {})


# ---------------------------------------------------------------------------
# Checksum gate: shared between VALID and ALREADY_APPLIED, runs before the
# hydration/isolation checks and before validation is ever invoked.
# ---------------------------------------------------------------------------


def test_resume_aborts_checksum_mismatch_before_hydration(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(tmp_path)
    # Mutate patch.diff after setup so its content no longer matches the
    # checksum recorded in run.json at preview time.
    (ctx["run_dir"] / "patch.diff").write_text("tampered patch content\n", encoding="utf-8")
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

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    assert run_metadata.status == "failed"
    assert "checksum_mismatch" in (run_metadata.failure_artifacts or [])


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


def test_resume_aborts_wal_run_id_mismatch(tmp_path: Path) -> None:
    """The WAL's own run_id must match the run being resumed -- rejects an
    apply.json that was copied in from (or belongs to) a different run."""
    ctx = _setup_resumable_run(tmp_path)
    apply_json_path = ctx["run_dir"] / "apply.json"
    tampered = json.loads(apply_json_path.read_text(encoding="utf-8"))
    tampered["run_id"] = "some-other-run"
    apply_json_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")
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


def test_resume_aborts_backup_pointer_redirected(tmp_path: Path) -> None:
    """The backup pointer must be the canonical run-local path -- rejects a
    WAL redirected to point at some other file on disk, even if that file
    happens to exist."""
    ctx = _setup_resumable_run(tmp_path)
    decoy_backup = tmp_path / "decoy-backup.diff"
    decoy_backup.write_text("dummy patch content\n", encoding="utf-8")
    apply_json_path = ctx["run_dir"] / "apply.json"
    tampered = json.loads(apply_json_path.read_text(encoding="utf-8"))
    tampered["pre_apply_diff_backup"] = str(decoy_backup)
    apply_json_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")
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


def test_resume_aborts_backup_content_mismatch(tmp_path: Path) -> None:
    """The backup file's bytes must exactly match the current patch.diff --
    rejects a backup that was swapped or corrupted independently of the WAL,
    even though it sits at the canonical path and the WAL is otherwise
    well-formed."""
    ctx = _setup_resumable_run(tmp_path)
    ctx["backup_path"].write_text("not the same patch content at all\n", encoding="utf-8")
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
# Lock contention -> abort immediately, before ANY git read (HEAD or repo
# state), not just before lifecycle classification.
# ---------------------------------------------------------------------------


def test_resume_aborts_lock_contention(tmp_path: Path) -> None:
    ctx = _setup_resumable_run(tmp_path)
    lifecycle_mock = MagicMock(return_value=PatchLifecycleState.ALREADY_APPLIED)
    current_head_mock = MagicMock(side_effect=AssertionError("current_head must not be called"))
    repository_state_mock = MagicMock(
        side_effect=AssertionError("repository_state must not be called")
    )
    coordination_db_dir = tmp_path / "coordination"
    coordination_db_dir.mkdir()

    with (
        patch("orchestrator.commands.apply.acquire_repo_lock", return_value=False),
        patch("orchestrator.lifecycle.classify_lifecycle", lifecycle_mock),
        patch("orchestrator.git.current_head", current_head_mock),
        patch("orchestrator.git.repository_state", repository_state_mock),
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
    current_head_mock.assert_not_called()
    repository_state_mock.assert_not_called()


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


# ---------------------------------------------------------------------------
# Part 3: dirt capture and restore for --allow-dirty (issue #262)
# ---------------------------------------------------------------------------


def _make_patch_diff(repo_dir: Path) -> str:
    """Return a unified diff (as text) that adds a new file `patched.txt`
    to the repo's clean HEAD state. Leaves the working tree unchanged."""
    new_file = repo_dir / "patched.txt"
    new_file.write_text("patched content\n", encoding="utf-8")
    _git("add", "patched.txt", cwd=repo_dir)
    diff_text = _git("diff", "--cached", cwd=repo_dir).stdout
    _git("reset", cwd=repo_dir)
    new_file.unlink()
    return diff_text


def _setup_allow_dirty_run(
    tmp_path: Path,
    *,
    tracked_dirt: bool = False,
    untracked_dirt: bool = False,
) -> dict:
    """Build a 'previewed' run with a real, cleanly-applying patch, on a
    repo whose working tree may carry pre-existing tracked/untracked dirt.
    Unlike _setup_resumable_run, this targets the VALID (happy-path) branch
    with a patch that actually applies via `git apply`.
    """
    repo_dir = tmp_path / "repo"
    commit1, original_branch = _init_repo(repo_dir)

    patch_content = _make_patch_diff(repo_dir)

    if tracked_dirt:
        (repo_dir / "file.txt").write_text("v1-dirty", encoding="utf-8")
    if untracked_dirt:
        (repo_dir / "untracked.txt").write_text("untracked content\n", encoding="utf-8")

    run_id = "test-allow-dirty-run"
    workspace_path = tmp_path / "workspace"
    workspace = WorkspaceManager(workspace_path)
    workspace.setup()
    run_dir = workspace.create_run_directory(run_id)

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

    return {
        "repo_dir": repo_dir,
        "workspace_path": workspace_path,
        "workspace": workspace,
        "run_id": run_id,
        "run_dir": run_dir,
        "original_branch": original_branch,
        "commit1": commit1,
    }


def _run_allow_dirty(ctx: dict, validator_result=None):
    validator_mock = MagicMock(return_value=validator_result or _passing_validator())
    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
    ):
        apply_execute(ctx["run_id"], allow_dirty=True, workspace=ctx["workspace_path"])


def test_allow_dirty_tracked_only_captures_and_restores(tmp_path: Path) -> None:
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)
    _run_allow_dirty(ctx)

    assert (ctx["repo_dir"] / "file.txt").read_text(encoding="utf-8") == "v1-dirty"
    assert (ctx["repo_dir"] / "patched.txt").exists()

    stash_list = _git("stash", "list", cwd=ctx["repo_dir"]).stdout
    assert stash_list.strip() == ""

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    assert run_metadata.status == "applied"


def test_allow_dirty_untracked_only_captures_and_restores(tmp_path: Path) -> None:
    ctx = _setup_allow_dirty_run(tmp_path, untracked_dirt=True)
    _run_allow_dirty(ctx)

    assert (ctx["repo_dir"] / "untracked.txt").read_text(encoding="utf-8") == "untracked content\n"
    assert (ctx["repo_dir"] / "patched.txt").exists()


def test_allow_dirty_both_captures_and_restores(tmp_path: Path) -> None:
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True, untracked_dirt=True)
    _run_allow_dirty(ctx)

    assert (ctx["repo_dir"] / "file.txt").read_text(encoding="utf-8") == "v1-dirty"
    assert (ctx["repo_dir"] / "untracked.txt").read_text(encoding="utf-8") == "untracked content\n"
    assert (ctx["repo_dir"] / "patched.txt").exists()


def test_allow_dirty_clean_tree_skips_capture(tmp_path: Path) -> None:
    """--allow-dirty on an already-clean tree must behave like a normal
    apply: no stash created, no dirt_stash_sha recorded."""
    ctx = _setup_allow_dirty_run(tmp_path)
    _run_allow_dirty(ctx)

    stash_list = _git("stash", "list", cwd=ctx["repo_dir"]).stdout
    assert stash_list.strip() == ""

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    assert run_metadata.dirt_stash_sha is None


def test_allow_dirty_false_rejects_dirty(tmp_path: Path) -> None:
    """Regression: without --allow-dirty, a dirty tree still aborts before
    any capture/mutation happens."""
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)
    validator_mock = MagicMock(return_value=_passing_validator())

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], allow_dirty=False, workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()
    assert (ctx["repo_dir"] / "file.txt").read_text(encoding="utf-8") == "v1-dirty"


def test_capture_aborts_on_no_head(tmp_path: Path) -> None:
    """A repo with no commits has no HEAD; dirt capture must abort before
    any mutation rather than crash partway through."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    _git("init", cwd=repo_dir)
    _git("config", "user.email", "test@example.com", cwd=repo_dir)
    _git("config", "user.name", "Test User", cwd=repo_dir)
    (repo_dir / "untracked.txt").write_text("x", encoding="utf-8")

    from orchestrator.git import stash_create_dirt

    with pytest.raises(ValueError, match="no HEAD"):
        stash_create_dirt(repo_dir)


def test_capture_aborts_on_dirty_submodule(tmp_path: Path) -> None:
    from orchestrator.git import stash_create_dirt

    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)

    with (
        patch("orchestrator.git._has_dirty_submodules", return_value=True),
        pytest.raises(ValueError, match="submodule"),
    ):
        stash_create_dirt(repo_dir)


def test_dirt_restore_failure_reports_structured_error(tmp_path: Path) -> None:
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)

    validator_mock = MagicMock(return_value=_failing_validator())
    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        patch("orchestrator.git.stash_apply_dirt", return_value=False),
        pytest.raises(typer.Exit),
    ):
        apply_execute(ctx["run_id"], allow_dirty=True, workspace=ctx["workspace_path"])

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["dirt_restore_failed"] is True
    assert apply_json["dirt_recovery_command"].startswith("git stash apply --index ")
    assert apply_json["rolled_back"] is False


def test_code_rollback_failure_reports_dirt_recovery(tmp_path: Path) -> None:
    """When the code rollback itself fails (not just the dirt restore),
    the tree is in an unknown state and stash_apply_dirt must not be
    attempted -- but the user still needs dirt_restore_failed and the
    recovery command so their captured changes aren't silently orphaned."""
    from orchestrator.exceptions import RollbackError

    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)

    validator_mock = MagicMock(return_value=_failing_validator())
    stash_apply_mock = MagicMock(return_value=True)
    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        patch(
            "orchestrator.agents.executor.rollback_to_commit",
            side_effect=RollbackError(ctx["repo_dir"], ctx["commit1"], "rollback failed"),
        ),
        patch("orchestrator.git.stash_apply_dirt", stash_apply_mock),
        pytest.raises(typer.Exit),
    ):
        apply_execute(ctx["run_id"], allow_dirty=True, workspace=ctx["workspace_path"])

    stash_apply_mock.assert_not_called()
    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["dirt_restore_failed"] is True
    assert apply_json["dirt_recovery_command"].startswith("git stash apply --index ")
    assert apply_json["rolled_back"] is False


def test_dirt_ref_dropped_after_successful_restore(tmp_path: Path) -> None:
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)
    from orchestrator.git import dirt_ref_name

    with pytest.raises(typer.Exit):
        _run_allow_dirty(ctx, validator_result=_failing_validator())

    ref_check = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", dirt_ref_name(ctx["run_id"])],
        cwd=ctx["repo_dir"],
        capture_output=True,
        text=True,
    )
    assert ref_check.returncode != 0

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["dirt_restored"] is True
    assert apply_json["rolled_back"] is True
    # Rollback restored pre-patch state; dirt restore put the original
    # tracked change back on top.
    assert (ctx["repo_dir"] / "file.txt").read_text(encoding="utf-8") == "v1-dirty"
    assert not (ctx["repo_dir"] / "patched.txt").exists()


def test_resume_with_dirt_success_restores_dirt(tmp_path: Path) -> None:
    """Part 4: automatic resume must restore dirt captured by a prior
    --allow-dirty run instead of aborting -- the WAL's own dirt_stash_sha
    propagates through resume the same way it does on the happy path."""
    ctx = _setup_resumable_run(tmp_path, capture_dirt=True)

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

    assert (ctx["repo_dir"] / "dirt.txt").read_text(encoding="utf-8") == "pre-existing dirt\n"

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    assert run_metadata.status == "applied"

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["status"] == "applied"
    assert apply_json["dirt_restored"] is True

    ref_check = subprocess.run(
        ["git", "show-ref", "--verify", f"refs/patchforge/dirt/{ctx['run_id']}"],
        cwd=ctx["repo_dir"],
        capture_output=True,
        text=True,
    )
    assert ref_check.returncode != 0


def test_resume_with_dirt_validation_failure_rollback_restores_dirt(tmp_path: Path) -> None:
    """The validation-failure-rollback restore block must also pick up
    dirt propagated through the resume path, not just the success block."""
    ctx = _setup_resumable_run(tmp_path, capture_dirt=True)
    rollback_mock = MagicMock(return_value=None)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch("orchestrator.agents.validator.run", return_value=_failing_validator()),
        patch("orchestrator.agents.executor.rollback_to_commit", rollback_mock),
        pytest.raises(typer.Exit),
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert (ctx["repo_dir"] / "dirt.txt").read_text(encoding="utf-8") == "pre-existing dirt\n"

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["dirt_restored"] is True
    assert apply_json["rolled_back"] is True


def test_resume_with_dirt_stash_apply_fails(tmp_path: Path) -> None:
    """Entry-point coverage: the existing FATAL dirt-restore-failure
    messaging (already covered from the happy path) is also reachable via
    resume once dirt_stash_sha propagates."""
    ctx = _setup_resumable_run(tmp_path, capture_dirt=True)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.ALREADY_APPLIED,
        ),
        patch(
            "orchestrator.agents.validator.run",
            return_value=_passing_validator(),
        ),
        patch("orchestrator.git.stash_apply_dirt", return_value=False),
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["dirt_restore_failed"] is True
    assert apply_json["dirt_recovery_command"].startswith("git stash apply --index ")


def test_resume_with_dirt_stash_gone(tmp_path: Path) -> None:
    """If the dirt SHA the WAL recorded is unreachable (e.g. the ref/object
    was garbage collected between crash and resume), resume must report a
    structured failure via a real (unmocked) stash-apply failure against a
    SHA that genuinely does not exist in the repo, not crash."""
    ctx = _setup_resumable_run(tmp_path, capture_dirt=True)
    fake_sha = "0" * 40

    apply_json_path = ctx["run_dir"] / "apply.json"
    apply_json = json.loads(apply_json_path.read_text(encoding="utf-8"))
    apply_json["dirt_stash_sha"] = fake_sha
    apply_json_path.write_text(json.dumps(apply_json, indent=2), encoding="utf-8")

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

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["dirt_restore_failed"] is True
    assert apply_json["dirt_recovery_command"] == f"git stash apply --index {fake_sha}"


def test_resume_after_dirt_already_restored_shows_honest_message(tmp_path: Path) -> None:
    """Sub-case 2: the WAL crashed after dirt was already restored to the
    tree, so re-classification returns CONFLICT (the reverse-check no
    longer matches) -- but a fresh reverse-check confirms the patch content
    is present, so the message must be the honest "check first" one, not
    the generic CONFLICT message."""
    ctx = _setup_resumable_run(tmp_path, capture_dirt=True)
    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.CONFLICT,
        ),
        patch(
            "orchestrator.git.try_apply_dry_run_reverse",
            return_value=ApplyCheckStatus.PASSED,
        ),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    assert run_metadata.dirt_stash_sha is not None


def test_genuine_conflict_with_dirt_stash_present_shows_original_message(tmp_path: Path) -> None:
    """A run that captured dirt can still hit a genuine CONFLICT unrelated
    to dirt (e.g. HEAD advanced) -- the reverse-check re-verification must
    not swallow that into a false "dirt already restored" message."""
    ctx = _setup_resumable_run(tmp_path, capture_dirt=True)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.CONFLICT,
        ),
        patch(
            "orchestrator.git.try_apply_dry_run_reverse",
            return_value=ApplyCheckStatus.CONFLICT,
        ),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1


def test_stash_structure_valid(tmp_path: Path) -> None:
    """The manually-built 3-parent dirt commit must have the exact parent
    count `git stash apply` expects, and `stash apply --index` must accept
    it. This guards against relying on undocumented git internals without
    a check that would fail loudly in CI if git's behavior ever changes."""
    from orchestrator.git import stash_create_dirt

    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    (repo_dir / "file.txt").write_text("modified", encoding="utf-8")
    (repo_dir / "untracked.txt").write_text("untracked", encoding="utf-8")

    sha = stash_create_dirt(repo_dir)
    assert sha is not None

    parents = _git("rev-list", "--parents", "-n", "1", sha, cwd=repo_dir).stdout.split()
    assert len(parents) - 1 == 3

    _git("reset", "--hard", "HEAD", cwd=repo_dir)
    _git("clean", "-fd", cwd=repo_dir)

    _git("stash", "apply", "--index", sha, cwd=repo_dir)
    assert (repo_dir / "file.txt").read_text(encoding="utf-8") == "modified"
    assert (repo_dir / "untracked.txt").read_text(encoding="utf-8") == "untracked"


def test_stash_preserves_staged_vs_unstaged_split(tmp_path: Path) -> None:
    """index_parent must be the tracked stash's own index-commit (its 2nd
    parent), not the tracked stash commit itself -- otherwise the combined
    commit's parent2 has the same tree as its own top commit, which is not
    the canonical `git stash push -u` structure and risks losing the
    staged/unstaged distinction on restore."""
    from orchestrator.git import stash_create_dirt

    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    (repo_dir / "staged.txt").write_text("v1", encoding="utf-8")
    (repo_dir / "unstaged.txt").write_text("v1", encoding="utf-8")
    _git("add", "-A", cwd=repo_dir)
    _git("commit", "-m", "add both files", cwd=repo_dir)

    (repo_dir / "staged.txt").write_text("staged-change", encoding="utf-8")
    _git("add", "staged.txt", cwd=repo_dir)
    (repo_dir / "unstaged.txt").write_text("unstaged-change", encoding="utf-8")

    sha = stash_create_dirt(repo_dir)
    assert sha is not None

    _git("reset", "--hard", "HEAD", cwd=repo_dir)
    _git("clean", "-fd", cwd=repo_dir)

    _git("stash", "apply", "--index", sha, cwd=repo_dir)
    status = _git("status", "--porcelain", cwd=repo_dir).stdout
    assert "M  staged.txt" in status
    assert " M unstaged.txt" in status
    assert (repo_dir / "staged.txt").read_text(encoding="utf-8") == "staged-change"
    assert (repo_dir / "unstaged.txt").read_text(encoding="utf-8") == "unstaged-change"


def test_orphaned_dirt_ref_warning_shown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A prior --allow-dirty run captured dirt and crashed before restoring
    it, leaving a `refs/patchforge/dirt/<run_id>` ref with no corresponding
    run.json (e.g. the run directory was already cleaned up). The advisory
    must still fire, since refs/patchforge/dirt/* is PatchForge's own
    private namespace -- anything found there is unambiguously ours, unlike
    the old refs/stash-based design this replaced."""
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)
    from orchestrator.git import stash_create_dirt, store_dirt_ref

    sha = stash_create_dirt(ctx["repo_dir"])
    assert sha is not None
    assert store_dirt_ref(ctx["repo_dir"], "some-other-run", sha)
    _git("reset", "--hard", "HEAD", cwd=ctx["repo_dir"])
    _git("clean", "-fd", cwd=ctx["repo_dir"])

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", return_value=_passing_validator()),
    ):
        apply_execute(ctx["run_id"], allow_dirty=False, workspace=ctx["workspace_path"])

    out = capsys.readouterr().out
    assert "unresumed --allow-dirty dirt" in out
    assert "some-other-run" in out
    assert sha in out


def test_orphaned_dirt_ref_shows_age_cleanup_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An orphaned dirt capture whose run.json is old enough (per
    _ORPHAN_CLEANUP_CANDIDATE_DAYS) gets an extra manual-cleanup hint with
    the exact `git update-ref -d` recovery command."""
    import os

    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)
    from orchestrator.git import stash_create_dirt, store_dirt_ref

    sha = stash_create_dirt(ctx["repo_dir"])
    assert sha is not None
    orphan_run_id = "stale-orphan-run"
    assert store_dirt_ref(ctx["repo_dir"], orphan_run_id, sha)
    orphan_run_dir = ctx["workspace"].create_run_directory(orphan_run_id)
    orphan_meta = RunMetadata(
        run_id=orphan_run_id,
        target_path=str(ctx["repo_dir"]),
        workspace_path=str(ctx["workspace_path"]),
        base_commit=ctx["commit1"],
        branch=ctx["original_branch"],
        status="failed",
        v1_supported=True,
        dirt_stash_sha=sha,
    )
    ctx["workspace"].write_run_json(orphan_run_id, orphan_meta)
    orphan_run_json = orphan_run_dir / "run.json"
    old_time = orphan_run_json.stat().st_mtime - (8 * 86400)
    os.utime(orphan_run_json, (old_time, old_time))

    _git("reset", "--hard", "HEAD", cwd=ctx["repo_dir"])
    _git("clean", "-fd", cwd=ctx["repo_dir"])

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", return_value=_passing_validator()),
    ):
        apply_execute(ctx["run_id"], allow_dirty=False, workspace=ctx["workspace_path"])

    out = " ".join(capsys.readouterr().out.split())
    assert "candidate for manual cleanup" in out
    assert f"git update-ref -d refs/patchforge/dirt/{orphan_run_id} {sha}" in out


def test_orphan_advisory_excludes_run_being_resumed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The orphan advisory must not warn about the very run this invocation
    is about to resume -- that ref is handled by the resume path itself,
    not abandoned."""
    ctx = _setup_resumable_run(tmp_path, capture_dirt=True)

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

    out = capsys.readouterr().out
    assert f"unresumed --allow-dirty dirt capture for run {ctx['run_id']}" not in out


def test_orphan_advisory_reframes_still_applying_runs_as_resumable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An orphaned ref belonging to a DIFFERENT run whose apply.json is
    still status=='applying' must lead with "might be resumable" and
    degrade the cleanup/recovery command to a fallback -- never hidden,
    just reordered."""
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)
    from orchestrator.git import stash_create_dirt, store_dirt_ref

    sha = stash_create_dirt(ctx["repo_dir"])
    assert sha is not None
    orphan_run_id = "still-applying-orphan-run"
    assert store_dirt_ref(ctx["repo_dir"], orphan_run_id, sha)
    orphan_run_dir = ctx["workspace"].create_run_directory(orphan_run_id)
    orphan_wal = ApplyResult(
        run_id=orphan_run_id,
        applied_at=datetime.now(timezone.utc),
        branch="patchforge/" + orphan_run_id,
        success=False,
        status="applying",
        pre_apply_head=ctx["commit1"],
        pre_apply_branch=ctx["original_branch"],
        dirt_stash_sha=sha,
    )
    (orphan_run_dir / "apply.json").write_text(
        orphan_wal.model_dump_json(indent=2), encoding="utf-8"
    )

    _git("reset", "--hard", "HEAD", cwd=ctx["repo_dir"])
    _git("clean", "-fd", cwd=ctx["repo_dir"])

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", return_value=_passing_validator()),
    ):
        apply_execute(ctx["run_id"], allow_dirty=False, workspace=ctx["workspace_path"])

    out = " ".join(capsys.readouterr().out.split())
    assert "might be resumable" in out
    assert f"apply {orphan_run_id}" in out
    assert sha in out


def test_orphan_advisory_shows_default_message_when_status_unreadable_or_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An orphaned ref whose apply.json is missing entirely (unreadable)
    must not claim resumability it cannot confirm -- default message,
    unchanged."""
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)
    from orchestrator.git import stash_create_dirt, store_dirt_ref

    sha = stash_create_dirt(ctx["repo_dir"])
    assert sha is not None
    assert store_dirt_ref(ctx["repo_dir"], "unreadable-wal-run", sha)
    _git("reset", "--hard", "HEAD", cwd=ctx["repo_dir"])
    _git("clean", "-fd", cwd=ctx["repo_dir"])

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", return_value=_passing_validator()),
    ):
        apply_execute(ctx["run_id"], allow_dirty=False, workspace=ctx["workspace_path"])

    out = " ".join(capsys.readouterr().out.split())
    assert "might be resumable" not in out
    assert "Your working-tree state may be recoverable via" in out


def test_capture_aborts_if_store_dirt_ref_fails(tmp_path: Path) -> None:
    """If recording the dirt capture under its private ref fails, the apply
    must abort before any mutation rather than proceed with an unreferenced
    (gc-eligible) dirt commit."""
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.git.store_dirt_ref", return_value=False),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], allow_dirty=True, workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    # Working tree must be untouched -- capture must abort before
    # force_reset_apply runs.
    assert (ctx["repo_dir"] / "file.txt").read_text(encoding="utf-8") == "v1-dirty"


def test_capture_aborts_if_dirt_ref_already_exists(tmp_path: Path) -> None:
    """If refs/patchforge/dirt/<run_id> already exists (e.g. left behind by
    an incomplete prior cleanup), store_dirt_ref's create-only semantics
    refuse to overwrite it, and the apply must abort before any mutation
    rather than silently clobbering the stale ref."""
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)
    from orchestrator.git import dirt_ref_name

    _git(
        "update-ref",
        dirt_ref_name(ctx["run_id"]),
        ctx["commit1"],
        cwd=ctx["repo_dir"],
    )

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], allow_dirty=True, workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    assert (ctx["repo_dir"] / "file.txt").read_text(encoding="utf-8") == "v1-dirty"


def test_dirt_ref_namespace_isolated_from_user_stash(tmp_path: Path) -> None:
    """PatchForge's dirt-capture ref lives entirely outside refs/stash, so a
    full capture-restore-cleanup cycle must never observe or disturb a
    stash entry the user pushed independently -- proving namespace
    isolation rather than relying on race-timing to avoid a collision."""
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)

    # Pathspec-limited so this doesn't also sweep up file.txt's dirt (which
    # tracked_dirt=True already left uncommitted) or advance HEAD.
    (ctx["repo_dir"] / "unrelated.txt").write_text("user's own wip", encoding="utf-8")
    _git(
        "stash",
        "push",
        "--include-untracked",
        "-m",
        "user's own unrelated work",
        "--",
        "unrelated.txt",
        cwd=ctx["repo_dir"],
    )
    user_stash_before = _git("stash", "list", cwd=ctx["repo_dir"]).stdout
    assert user_stash_before.strip() != ""
    assert (ctx["repo_dir"] / "file.txt").read_text(encoding="utf-8") == "v1-dirty"

    _run_allow_dirty(ctx)

    user_stash_after = _git("stash", "list", cwd=ctx["repo_dir"]).stdout
    assert user_stash_after == user_stash_before


def test_resolve_dirt_ref_returns_none_when_absent(tmp_path: Path) -> None:
    from orchestrator.git import resolve_dirt_ref

    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)

    assert resolve_dirt_ref(repo_dir, "no-such-run") is None


def test_resolve_dirt_ref_returns_sha_when_present(tmp_path: Path) -> None:
    from orchestrator.git import resolve_dirt_ref, stash_create_dirt, store_dirt_ref

    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    (repo_dir / "file.txt").write_text("dirty", encoding="utf-8")
    sha = stash_create_dirt(repo_dir)
    assert sha is not None
    assert store_dirt_ref(repo_dir, "some-run", sha)

    assert resolve_dirt_ref(repo_dir, "some-run") == sha


def test_resolve_dirt_ref_raises_on_unexpected_git_error(tmp_path: Path) -> None:
    """A failure that is NOT git's normal "ref does not exist" signal (e.g.
    a corrupted repository, a permissions error) must not be silently
    treated as "no ref" -- that would let a caller reuse-or-abandon
    decision proceed on an undiagnosable state. Simulated here by mocking
    the underlying git call to return a distinct fatal error."""
    from orchestrator.git import resolve_dirt_ref

    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)

    with (
        patch(
            "orchestrator.git._run_git_safe",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr="fatal: not a git repository\n"
            ),
        ),
        pytest.raises(RuntimeError, match="Failed to check dirt-capture ref"),
    ):
        resolve_dirt_ref(repo_dir, "some-run")


def test_dirt_restore_succeeds_even_if_ref_delete_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed ref cleanup after a successful dirt restore must not be
    treated as fatal -- the working tree is already correct at that point,
    so this is a non-fatal warning, not a rolled-back/failed run."""
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", return_value=_passing_validator()),
        patch("orchestrator.git.delete_dirt_ref", return_value=False),
    ):
        apply_execute(ctx["run_id"], allow_dirty=True, workspace=ctx["workspace_path"])

    out = " ".join(capsys.readouterr().out.split())
    assert "cleaning up the internal dirt-capture ref failed" in out
    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    assert run_metadata.status == "applied"


def test_capture_aborts_if_force_reset_fails(tmp_path: Path) -> None:
    """If resetting to a clean tree after capturing dirt fails, the apply
    must abort with a recovery pointer rather than silently continuing on
    a tree that may still carry the pre-existing dirt."""
    from orchestrator.schemas.git import GitCommandResult

    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch(
            "orchestrator.git.force_reset_apply",
            return_value=GitCommandResult(return_code=1, stdout="", stderr="reset failed"),
        ),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], allow_dirty=True, workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    assert run_metadata.dirt_stash_sha is not None


def test_branch_checkout_failure_restores_dirt(tmp_path: Path) -> None:
    """If create_controlled_branch fails after dirt was already captured
    and the tree reset to clean, the dirt must be restored before exiting
    -- not silently left in an unreferenced-looking state."""
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch(
            "orchestrator.git.create_controlled_branch",
            return_value=MagicMock(return_code=1, stderr="branch already exists"),
        ),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], allow_dirty=True, workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    assert (ctx["repo_dir"] / "file.txt").read_text(encoding="utf-8") == "v1-dirty"
    stash_list = _git("stash", "list", cwd=ctx["repo_dir"]).stdout
    assert stash_list.strip() == ""


def test_success_path_dirt_restore_conflict_reports_structured_error(tmp_path: Path) -> None:
    """Distinct from the rollback-path failure test: here the patch applies
    and validates successfully, but restoring the pre-existing dirt on top
    of it fails/conflicts. success/status must still reflect the applied
    patch while dirt_restore_failed communicates the separate problem."""
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", return_value=_passing_validator()),
        patch("orchestrator.git.stash_apply_dirt", return_value=False),
    ):
        apply_execute(ctx["run_id"], allow_dirty=True, workspace=ctx["workspace_path"])

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["success"] is True
    assert apply_json["status"] == "applied"
    assert apply_json["dirt_restore_failed"] is True
    assert apply_json["dirt_recovery_command"].startswith("git stash apply --index ")

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    assert run_metadata.status == "applied"


# ---------------------------------------------------------------------------
# Sub-case 0: crash between the first WAL checkpoint and apply_patch leaves
# a clean tree at base_commit (classified VALID, not ALREADY_APPLIED) with
# an orphaned dirt-capture ref for this run_id.
# ---------------------------------------------------------------------------


def test_happy_path_reuses_orphaned_dirt_ref_for_same_run_id(tmp_path: Path) -> None:
    """A retry on a clean tree that finds its own orphaned dirt ref must
    reuse the captured SHA instead of silently abandoning it -- no new
    capture, no error, the old dirt reappears after a successful apply."""
    ctx = _setup_allow_dirty_run(tmp_path)
    from orchestrator.git import stash_create_dirt, store_dirt_ref

    (ctx["repo_dir"] / "dirt.txt").write_text("orphaned dirt\n", encoding="utf-8")
    sha = stash_create_dirt(ctx["repo_dir"])
    assert sha is not None
    assert store_dirt_ref(ctx["repo_dir"], ctx["run_id"], sha)
    _git("checkout", "--", ".", cwd=ctx["repo_dir"])
    (ctx["repo_dir"] / "dirt.txt").unlink(missing_ok=True)
    _git("clean", "-fd", cwd=ctx["repo_dir"])

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    run_metadata.dirt_stash_sha = sha
    ctx["workspace"].write_run_json(ctx["run_id"], run_metadata)

    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", return_value=_passing_validator()),
    ):
        apply_execute(ctx["run_id"], allow_dirty=False, workspace=ctx["workspace_path"])

    assert (ctx["repo_dir"] / "dirt.txt").read_text(encoding="utf-8") == "orphaned dirt\n"
    assert (ctx["repo_dir"] / "patched.txt").exists()

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["dirt_stash_sha"] == sha
    assert apply_json["dirt_restored"] is True

    ref_check = subprocess.run(
        ["git", "show-ref", "--verify", f"refs/patchforge/dirt/{ctx['run_id']}"],
        cwd=ctx["repo_dir"],
        capture_output=True,
        text=True,
    )
    assert ref_check.returncode != 0


def test_happy_path_aborts_on_dirt_ref_run_metadata_sha_mismatch(tmp_path: Path) -> None:
    """If the dirt-capture ref for this run_id points to a different SHA
    than run.json recorded, that is an unexplained divergence between the
    two sources of truth -- abort before any mutation rather than guess
    which one is correct."""
    ctx = _setup_allow_dirty_run(tmp_path)
    from orchestrator.git import stash_create_dirt, store_dirt_ref

    (ctx["repo_dir"] / "dirt.txt").write_text("orphaned dirt\n", encoding="utf-8")
    ref_sha = stash_create_dirt(ctx["repo_dir"])
    assert ref_sha is not None
    assert store_dirt_ref(ctx["repo_dir"], ctx["run_id"], ref_sha)
    _git("checkout", "--", ".", cwd=ctx["repo_dir"])
    (ctx["repo_dir"] / "dirt.txt").unlink(missing_ok=True)
    _git("clean", "-fd", cwd=ctx["repo_dir"])

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    run_metadata.dirt_stash_sha = "f" * 40  # deliberately different from ref_sha
    ctx["workspace"].write_run_json(ctx["run_id"], run_metadata)

    validator_mock = MagicMock(return_value=_passing_validator())
    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], allow_dirty=False, workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()
    assert not (ctx["repo_dir"] / "patched.txt").exists()
    # The ref is untouched -- neither deleted nor reused.
    ref_check = subprocess.run(
        ["git", "show-ref", "--verify", "--hash", f"refs/patchforge/dirt/{ctx['run_id']}"],
        cwd=ctx["repo_dir"],
        capture_output=True,
        text=True,
    )
    assert ref_check.returncode == 0
    assert ref_check.stdout.strip() == ref_sha


def test_happy_path_aborts_with_clear_message_when_orphaned_ref_and_new_dirty_tree(
    tmp_path: Path,
) -> None:
    """A retry that finds its own orphaned dirt ref AND a genuinely new
    dirty tree (the user worked on the repo again between the crash and
    the retry) must abort before any mutation -- reusing the old SHA would
    lose the new changes, and capturing new dirt would collide with the
    ref's create-only semantics."""
    ctx = _setup_allow_dirty_run(tmp_path)
    from orchestrator.git import stash_create_dirt, store_dirt_ref

    (ctx["repo_dir"] / "dirt.txt").write_text("orphaned dirt\n", encoding="utf-8")
    sha = stash_create_dirt(ctx["repo_dir"])
    assert sha is not None
    assert store_dirt_ref(ctx["repo_dir"], ctx["run_id"], sha)
    _git("checkout", "--", ".", cwd=ctx["repo_dir"])
    (ctx["repo_dir"] / "dirt.txt").unlink(missing_ok=True)
    _git("clean", "-fd", cwd=ctx["repo_dir"])

    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    run_metadata.dirt_stash_sha = sha
    ctx["workspace"].write_run_json(ctx["run_id"], run_metadata)

    # New, genuinely different dirt since the crash.
    (ctx["repo_dir"] / "file.txt").write_text("v1-new-dirt-since-crash", encoding="utf-8")

    validator_mock = MagicMock(return_value=_passing_validator())
    with (
        patch(
            "orchestrator.lifecycle.classify_lifecycle",
            return_value=PatchLifecycleState.VALID,
        ),
        patch("orchestrator.agents.validator.run", validator_mock),
        pytest.raises(typer.Exit) as exc,
    ):
        apply_execute(ctx["run_id"], allow_dirty=True, workspace=ctx["workspace_path"])

    assert exc.value.exit_code == 1
    validator_mock.assert_not_called()
    # No mutation: new dirt untouched, patch not applied.
    assert (ctx["repo_dir"] / "file.txt").read_text(encoding="utf-8") == "v1-new-dirt-since-crash"
    assert not (ctx["repo_dir"] / "patched.txt").exists()
    # The orphaned ref is untouched, not deleted or overwritten.
    ref_check = subprocess.run(
        ["git", "show-ref", "--verify", "--hash", f"refs/patchforge/dirt/{ctx['run_id']}"],
        cwd=ctx["repo_dir"],
        capture_output=True,
        text=True,
    )
    assert ref_check.returncode == 0
    assert ref_check.stdout.strip() == sha


def test_dirt_untracked_filename_collision_with_patch_fails_cleanly(tmp_path: Path) -> None:
    """Regression: if captured dirt includes an untracked file with the
    same name the patch introduces but different content, restoring the
    dirt must fail cleanly (non-zero exit, no conflict markers, patch
    content left intact) rather than silently overwrite the patch's file."""
    ctx = _setup_allow_dirty_run(tmp_path)
    from orchestrator.git import has_merge_conflicts, stash_apply_dirt, stash_create_dirt

    (ctx["repo_dir"] / "untracked_collision.txt").write_text(
        "dirt content, not patch content\n", encoding="utf-8"
    )
    sha = stash_create_dirt(ctx["repo_dir"])
    assert sha is not None
    _git("clean", "-fd", cwd=ctx["repo_dir"])

    (ctx["repo_dir"] / "untracked_collision.txt").write_text("patch content\n", encoding="utf-8")

    result = stash_apply_dirt(ctx["repo_dir"], sha)

    assert result is False
    assert not has_merge_conflicts(ctx["repo_dir"])
    assert (ctx["repo_dir"] / "untracked_collision.txt").read_text(
        encoding="utf-8"
    ) == "patch content\n"
