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


@pytest.mark.unit
def test_v2_candidate_promotion_uses_real_validator_and_writes_authorized_evidence(
    tmp_path: Path,
) -> None:
    ctx = _setup_run(tmp_path, v2=True)

    apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))

    artifact = Path(ctx["workspace"]) / "runs" / str(ctx["run_id"]) / VALIDATION_JSON
    output = ValidatorOutput.model_validate_json(artifact.read_text(encoding="utf-8"))
    assert output.decision is not None and output.decision.authorized is True
    assert output.validation_subject is not None
    assert output.validation_subject.candidate_commit is not None


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

    def validator(*, config):
        (config.target_path / "validator.log").write_text("artifact\n", encoding="utf-8")
        return ValidatorOutput(overall_passed=True, run_id=str(ctx["run_id"])), {}

    with patch("orchestrator.agents.validator.run", side_effect=validator):
        apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))

    repo = Path(ctx["repo"])
    assert _git(repo, "show", f"patchforge/{ctx['run_id']}:README.md") == "Hello candidate"


@pytest.mark.unit
def test_recovery_finishes_only_matching_candidate_and_receipt(tmp_path: Path) -> None:
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
        expected_base_ref=f"refs/heads/{ctx['branch']}",
        expected_base_commit=str(ctx["base"]),
        policy_digest=policy.digest,
        workspace_path=str(Path(ctx["workspace"])),
    )
    _wal_write(wal, manager.run_dir(str(ctx["run_id"])) / "apply.json")

    apply_execute(str(ctx["run_id"]), workspace=Path(ctx["workspace"]))

    assert manager.read_run_json(str(ctx["run_id"])).status == "applied"


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
