"""Candidate-promotion recovery and isolation tests for issue #282 Phase 4."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from orchestrator.commands.apply import APPLY_PROTOCOL
from orchestrator.commands.apply import execute as apply_execute
from orchestrator.git import git_common_dir, promotion_receipt_ref
from orchestrator.schemas.artifacts import (
    VALIDATION_JSON,
    VALIDATION_POLICY_JSON,
    ApplyResult,
    RunMetadata,
)
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.validator_output import ValidatorOutput
from orchestrator.storage import _wal_write
from orchestrator.storage.lock import (
    acquire_candidate_promotion_lock,
    candidate_promotion_lock_path,
    release_candidate_promotion_lock,
)
from orchestrator.validation_decision import (
    attach_validation_decision,
    expected_validation_subject,
    validation_policy_for,
)
from orchestrator.workspace import WorkspaceManager


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup_run(tmp_path: Path, *, v2: bool = False) -> dict[str, object]:
    repo = tmp_path / "repo"
    workspace_path = tmp_path / "workspace"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("Hello\n", encoding="utf-8")
    if v2:
        (repo / "orchestrator.json").write_text(
            '{"schema_version":"2.0","validators":[{"id":"lint","adapter":"ruff"}]}',
            encoding="utf-8",
        )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    base = _git(repo, "rev-parse", "HEAD")
    branch = _git(repo, "branch", "--show-current")
    patch = (
        "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n"
        "@@ -1 +1 @@\n-Hello\n+Hello candidate\n"
    )
    manager = WorkspaceManager(workspace_path)
    manager.setup()
    run_id = "run_candidate"
    manager.create_run_directory(run_id)
    manager.write_run_json(
        run_id,
        RunMetadata(
            run_id=run_id,
            target_path=str(repo),
            workspace_path=str(workspace_path),
            base_commit=base,
            branch=branch,
            status="previewed",
            v1_supported=True,
            patch_checksum=hashlib.sha256(patch.encode()).hexdigest(),
        ),
    )
    manager.write_artifact(run_id, "patch.diff", patch)
    return {
        "repo": repo,
        "workspace": workspace_path,
        "manager": manager,
        "run_id": run_id,
        "base": base,
        "branch": branch,
    }


def _apply(ctx: dict[str, object]) -> None:
    with patch(
        "orchestrator.agents.validator.run",
        return_value=(ValidatorOutput(overall_passed=True), {}),
    ):
        apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))


@pytest.mark.unit
def test_candidate_promotion_leaves_checkout_and_branch_unchanged(tmp_path: Path) -> None:
    ctx = _setup_run(tmp_path)
    repo = Path(ctx["repo"])
    _apply(ctx)

    assert _git(repo, "branch", "--show-current") == ctx["branch"]
    assert _git(repo, "rev-parse", "HEAD") == ctx["base"]
    assert (repo / "README.md").read_text(encoding="utf-8") == "Hello\n"
    assert _git(repo, "show", f"patchforge/{ctx['run_id']}:README.md") == "Hello candidate"


@pytest.mark.integration
def test_v2_candidate_promotion_uses_real_validator_and_writes_authorized_evidence(
    tmp_path: Path,
) -> None:
    ctx = _setup_run(tmp_path, v2=True)

    apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))

    artifact = Path(ctx["workspace"]) / "runs" / str(ctx["run_id"]) / VALIDATION_JSON
    output = ValidatorOutput.model_validate_json(artifact.read_text(encoding="utf-8"))
    assert output.decision is not None and output.decision.authorized is True
    assert output.validation_subject is not None
    candidate_commit = output.validation_subject.candidate_commit
    assert candidate_commit is not None
    repo = Path(ctx["repo"])
    candidate_ref = f"refs/heads/patchforge/{ctx['run_id']}"
    receipt_ref = promotion_receipt_ref(str(ctx["run_id"]))
    assert _git(repo, "rev-parse", candidate_ref) == candidate_commit
    assert _git(repo, "rev-parse", receipt_ref) == candidate_commit
    assert _git(repo, "branch", "--show-current") == ctx["branch"]
    assert _git(repo, "rev-parse", "HEAD") == ctx["base"]
    assert (repo / "README.md").read_text(encoding="utf-8") == "Hello\n"
    assert _git(repo, "show", f"patchforge/{ctx['run_id']}:README.md") == "Hello candidate"


@pytest.mark.unit
def test_candidate_promotion_preserves_unrelated_dirty_tree(tmp_path: Path) -> None:
    ctx = _setup_run(tmp_path)
    repo = Path(ctx["repo"])
    (repo / "notes.txt").write_text("keep me\n", encoding="utf-8")

    _apply(ctx)

    assert (repo / "notes.txt").read_text(encoding="utf-8") == "keep me\n"
    assert "?? notes.txt" in _git(repo, "status", "--porcelain")


@pytest.mark.unit
def test_validator_untracked_artifact_does_not_block_promotion(tmp_path: Path) -> None:
    ctx = _setup_run(tmp_path)

    def validator(*, config, runtime):
        (config.target_path / "validator.log").write_text("artifact\n", encoding="utf-8")
        return ValidatorOutput(overall_passed=True, run_id=str(ctx["run_id"])), {}

    with patch("orchestrator.agents.validator.run", side_effect=validator):
        apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))

    repo = Path(ctx["repo"])
    assert _git(repo, "show", f"patchforge/{ctx['run_id']}:README.md") == "Hello candidate"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("wal_branch", "expected_status"), [(None, "applied"), ("other", "previewed")]
)
def test_recovery_finishes_after_base_advances_with_matching_candidate_and_receipt(
    tmp_path: Path,
    wal_branch: str | None,
    expected_status: str,
) -> None:
    ctx = _setup_run(tmp_path)
    repo = Path(ctx["repo"])
    manager = ctx["manager"]
    assert isinstance(manager, WorkspaceManager)
    candidate = _git(repo, "rev-parse", "HEAD")
    candidate_ref = f"refs/heads/patchforge/{ctx['run_id']}"
    receipt_ref = promotion_receipt_ref(str(ctx["run_id"]))
    _git(repo, "update-ref", candidate_ref, candidate)
    _git(repo, "update-ref", receipt_ref, candidate)
    config = TargetConfig.load(target_path=repo, workspace_path=Path(ctx["workspace"]))
    policy = validation_policy_for(config)
    subject = expected_validation_subject(
        run_id=str(ctx["run_id"]),
        project_root=repo,
        base_commit=str(ctx["base"]),
        patch_checksum=hashlib.sha256(
            (manager.run_dir(str(ctx["run_id"])) / "patch.diff").read_bytes()
        ).hexdigest(),
        candidate_commit=candidate,
        repository_identity=str(git_common_dir(repo)),
        policy_digest=policy.digest,
    )
    output = attach_validation_decision(
        ValidatorOutput(overall_passed=True, run_id=str(ctx["run_id"])),
        config,
        policy=policy,
        subject=subject,
    )
    manager.write_artifact(str(ctx["run_id"]), VALIDATION_POLICY_JSON, policy.model_dump_json())
    manager.write_artifact(str(ctx["run_id"]), VALIDATION_JSON, output.model_dump_json())
    wal = ApplyResult(
        run_id=str(ctx["run_id"]),
        applied_at=datetime.now(timezone.utc),
        branch=f"patchforge/{ctx['run_id']}",
        success=False,
        apply_protocol=APPLY_PROTOCOL,
        promotion_state="promotion_prepared",
        candidate_ref=candidate_ref,
        candidate_commit=candidate,
        promotion_receipt_ref=receipt_ref,
        expected_base_ref=f"refs/heads/{wal_branch or ctx['branch']}",
        expected_base_commit=str(ctx["base"]),
        policy_digest=policy.digest,
        workspace_path=str(Path(ctx["workspace"])),
    )
    _wal_write(wal, manager.run_dir(str(ctx["run_id"])) / "apply.json")
    (repo / "base-advanced.txt").write_text("new base\n", encoding="utf-8")
    (repo / "orchestrator.json").write_text("{not json", encoding="utf-8")
    _git(repo, "add", "base-advanced.txt", "orchestrator.json")
    _git(repo, "commit", "-m", "advance base")

    if expected_status == "applied":
        apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))
    else:
        with pytest.raises(typer.Exit):
            apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))

    assert manager.read_run_json(str(ctx["run_id"])).status == expected_status
    assert _git(repo, "rev-parse", "HEAD") != ctx["base"]


@pytest.mark.unit
def test_stale_base_without_published_recovery_rejects_before_candidate_construction(
    tmp_path: Path,
) -> None:
    ctx = _setup_run(tmp_path)
    repo = Path(ctx["repo"])
    manager = ctx["manager"]
    assert isinstance(manager, WorkspaceManager)
    (repo / "base-advanced.txt").write_text("new base\n", encoding="utf-8")
    _git(repo, "add", "base-advanced.txt")
    _git(repo, "commit", "-m", "advance base")

    with (
        patch("orchestrator.git.candidate_worktree") as candidate_worktree,
        pytest.raises(typer.Exit),
    ):
        apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))

    candidate_worktree.assert_not_called()
    assert manager.read_run_json(str(ctx["run_id"])).status == "previewed"
    assert (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/patchforge/{ctx['run_id']}"],
            cwd=repo,
            check=False,
        ).returncode
        != 0
    )


@pytest.mark.unit
def test_corrupt_candidate_recovery_wal_fails_closed_without_marking_run_applied(
    tmp_path: Path,
) -> None:
    ctx = _setup_run(tmp_path)
    manager = ctx["manager"]
    assert isinstance(manager, WorkspaceManager)
    (manager.run_dir(str(ctx["run_id"])) / "apply.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(typer.Exit):
        apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))

    assert manager.read_run_json(str(ctx["run_id"])).status == "previewed"
    lock = acquire_candidate_promotion_lock(git_common_dir(Path(ctx["repo"])))
    release_candidate_promotion_lock(lock)


@pytest.mark.unit
def test_linked_worktrees_share_candidate_promotion_lock_domain(tmp_path: Path) -> None:
    ctx = _setup_run(tmp_path)
    repo = Path(ctx["repo"])
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "--detach", str(linked), "HEAD")
    common_dir = git_common_dir(repo)
    linked_common_dir = git_common_dir(linked)
    first = acquire_candidate_promotion_lock(common_dir)
    try:
        assert candidate_promotion_lock_path(common_dir) == candidate_promotion_lock_path(
            linked_common_dir
        )
        with pytest.raises(RuntimeError, match="candidate-promotion coordination lock"):
            acquire_candidate_promotion_lock(linked_common_dir)
    finally:
        release_candidate_promotion_lock(first)
        _git(repo, "worktree", "remove", "--force", str(linked))

    second = acquire_candidate_promotion_lock(linked_common_dir)
    release_candidate_promotion_lock(second)


@pytest.mark.unit
def test_unrelated_repositories_do_not_contend_for_candidate_promotion_lock(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_ctx = _setup_run(first_root)
    second_ctx = _setup_run(second_root)
    first_common_dir = git_common_dir(Path(first_ctx["repo"]))
    second_common_dir = git_common_dir(Path(second_ctx["repo"]))

    first = acquire_candidate_promotion_lock(first_common_dir)
    second = acquire_candidate_promotion_lock(second_common_dir)
    try:
        assert candidate_promotion_lock_path(first_common_dir) != candidate_promotion_lock_path(
            second_common_dir
        )
    finally:
        release_candidate_promotion_lock(second)
        release_candidate_promotion_lock(first)


@pytest.mark.unit
def test_candidate_promotion_lock_fails_closed_when_database_cannot_open(tmp_path: Path) -> None:
    non_directory = tmp_path / "not-a-directory"
    non_directory.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RuntimeError, match="candidate-promotion coordination lock"):
        acquire_candidate_promotion_lock(non_directory)


@pytest.mark.unit
def test_candidate_promotion_lock_does_not_reenter(tmp_path: Path) -> None:
    ctx = _setup_run(tmp_path)
    common_dir = git_common_dir(Path(ctx["repo"]))
    first = acquire_candidate_promotion_lock(common_dir)
    try:
        with pytest.raises(RuntimeError, match="candidate-promotion coordination lock"):
            acquire_candidate_promotion_lock(common_dir)
    finally:
        release_candidate_promotion_lock(first)


@pytest.mark.unit
def test_apply_same_worker_id_cannot_reenter_candidate_promotion_lock(tmp_path: Path) -> None:
    ctx = _setup_run(tmp_path)
    common_dir = git_common_dir(Path(ctx["repo"]))
    first = acquire_candidate_promotion_lock(common_dir)
    try:
        with pytest.raises(typer.Exit):
            apply_execute(
                str(ctx["run_id"]),
                workspace=Path(ctx["workspace"]),
                worker_id=str(ctx["run_id"]),
            )
    finally:
        release_candidate_promotion_lock(first)


@pytest.mark.unit
def test_coordination_failure_blocks_recovery_before_wal_read(tmp_path: Path) -> None:
    ctx = _setup_run(tmp_path)
    manager = ctx["manager"]
    assert isinstance(manager, WorkspaceManager)
    wal_path = manager.run_dir(str(ctx["run_id"])) / "apply.json"
    wal_path.write_text("{not json", encoding="utf-8")
    before = wal_path.read_bytes()

    with (
        patch(
            "orchestrator.commands.apply.acquire_candidate_promotion_lock",
            side_effect=RuntimeError("database unavailable"),
        ),
        pytest.raises(typer.Exit),
    ):
        apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))

    assert wal_path.read_bytes() == before
    assert manager.read_run_json(str(ctx["run_id"])).status == "previewed"


@pytest.mark.unit
def test_coordination_failure_blocks_new_publication_without_writing_wal(tmp_path: Path) -> None:
    ctx = _setup_run(tmp_path)
    repo = Path(ctx["repo"])
    manager = ctx["manager"]
    assert isinstance(manager, WorkspaceManager)

    with (
        patch(
            "orchestrator.commands.apply.acquire_candidate_promotion_lock",
            side_effect=RuntimeError("database unavailable"),
        ),
        pytest.raises(typer.Exit),
    ):
        apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))

    assert not (manager.run_dir(str(ctx["run_id"])) / "apply.json").exists()
    assert manager.read_run_json(str(ctx["run_id"])).status == "previewed"
    assert (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/patchforge/{ctx['run_id']}"],
            cwd=repo,
            check=False,
        ).returncode
        != 0
    )


@pytest.mark.unit
@pytest.mark.parametrize("promotion_state", ["promotion_prepared", "promotion_applied"])
def test_partial_candidate_recovery_refs_fail_closed_without_marking_run_applied(
    tmp_path: Path, promotion_state: str
) -> None:
    ctx = _setup_run(tmp_path)
    repo = Path(ctx["repo"])
    manager = ctx["manager"]
    assert isinstance(manager, WorkspaceManager)
    candidate = _git(repo, "rev-parse", "HEAD")
    candidate_ref = f"refs/heads/patchforge/{ctx['run_id']}"
    _git(repo, "update-ref", candidate_ref, candidate)
    _wal_write(
        ApplyResult(
            run_id=str(ctx["run_id"]),
            applied_at=datetime.now(timezone.utc),
            branch=f"patchforge/{ctx['run_id']}",
            success=False,
            apply_protocol=APPLY_PROTOCOL,
            promotion_state=promotion_state,
            candidate_ref=candidate_ref,
            candidate_commit=candidate,
            promotion_receipt_ref=promotion_receipt_ref(str(ctx["run_id"])),
            expected_base_ref=f"refs/heads/{ctx['branch']}",
            expected_base_commit=str(ctx["base"]),
            policy_digest="a" * 64,
            workspace_path=str(Path(ctx["workspace"])),
        ),
        manager.run_dir(str(ctx["run_id"])) / "apply.json",
    )

    with pytest.raises(typer.Exit):
        apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))

    assert manager.read_run_json(str(ctx["run_id"])).status == "previewed"


@pytest.mark.unit
def test_foreign_apply_protocol_fails_closed(tmp_path: Path) -> None:
    ctx = _setup_run(tmp_path)
    manager = ctx["manager"]
    assert isinstance(manager, WorkspaceManager)
    _wal_write(
        ApplyResult(
            run_id=str(ctx["run_id"]),
            applied_at=datetime.now(timezone.utc),
            branch="legacy",
            success=False,
            apply_protocol="worker_legacy@1",
        ),
        manager.run_dir(str(ctx["run_id"])) / "apply.json",
    )

    with pytest.raises(typer.Exit):
        apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))
