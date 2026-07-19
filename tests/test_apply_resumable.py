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


def test_stash_dropped_after_successful_restore(tmp_path: Path) -> None:
    ctx = _setup_allow_dirty_run(tmp_path, tracked_dirt=True)
    with pytest.raises(typer.Exit):
        _run_allow_dirty(ctx, validator_result=_failing_validator())

    stash_list = _git("stash", "list", cwd=ctx["repo_dir"]).stdout
    assert stash_list.strip() == ""

    apply_json = json.loads((ctx["run_dir"] / "apply.json").read_text(encoding="utf-8"))
    assert apply_json["dirt_restored"] is True
    assert apply_json["rolled_back"] is True
    # Rollback restored pre-patch state; dirt restore put the original
    # tracked change back on top.
    assert (ctx["repo_dir"] / "file.txt").read_text(encoding="utf-8") == "v1-dirty"
    assert not (ctx["repo_dir"] / "patched.txt").exists()


def test_resume_aborts_if_dirt_stash_in_wal(tmp_path: Path) -> None:
    """The Part 3 / Part 4 contract: automatic resume must not silently
    proceed when the WAL recorded a dirt capture it doesn't know how to
    restore -- it must abort with a recovery pointer instead."""
    ctx = _setup_resumable_run(tmp_path)
    run_metadata = ctx["workspace"].read_run_json(ctx["run_id"])
    run_metadata.dirt_stash_sha = "deadbeef"
    ctx["workspace"].write_run_json(ctx["run_id"], run_metadata)

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

    apply_res = subprocess.run(
        ["git", "stash", "apply", "--index", sha],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    assert apply_res.returncode == 0
    assert (repo_dir / "file.txt").read_text(encoding="utf-8") == "modified"
    assert (repo_dir / "untracked.txt").read_text(encoding="utf-8") == "untracked"
