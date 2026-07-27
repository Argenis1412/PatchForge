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
from orchestrator.git import promotion_receipt_ref
from orchestrator.schemas.artifacts import ApplyResult, RunMetadata
from orchestrator.schemas.validator_output import ValidatorOutput
from orchestrator.storage import _wal_write
from orchestrator.workspace import WorkspaceManager


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup_run(tmp_path: Path) -> dict[str, object]:
    repo = tmp_path / "repo"
    workspace_path = tmp_path / "workspace"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("Hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
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
def test_candidate_promotion_preserves_unrelated_dirty_tree(tmp_path: Path) -> None:
    ctx = _setup_run(tmp_path)
    repo = Path(ctx["repo"])
    (repo / "notes.txt").write_text("keep me\n", encoding="utf-8")

    _apply(ctx)

    assert (repo / "notes.txt").read_text(encoding="utf-8") == "keep me\n"
    assert "?? notes.txt" in _git(repo, "status", "--porcelain")


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
