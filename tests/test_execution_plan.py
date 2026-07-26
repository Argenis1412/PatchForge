"""Execution-plan V2 validation and deterministic staging tests."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from orchestrator.agents.executor import run, run_execution_plan
from orchestrator.plan_validation import validate_execution_plan
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.execution_plan import (
    ExecutablePlanV2,
    ExecutionPlanContractError,
    ExecutionTask,
    FileEditOperation,
    MutationPreconditionError,
)
from orchestrator.storage.work_queue import _is_deterministic


def _git_repo(tmp_path: Path) -> tuple[Path, str]:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "worker.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "worker.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    return tmp_path, commit


def _plan(commit: str, *, path: str = "worker.py", expected: str | None = None) -> ExecutablePlanV2:
    return ExecutablePlanV2(
        plan_contract_version=2,
        base_commit=commit,
        tasks=[
            ExecutionTask(
                task_id="T1",
                operation=FileEditOperation(
                    kind="file_edit",
                    path=path,
                    expected_sha256=expected,
                    content="value = 2\n",
                ),
            )
        ],
    )


def test_execution_plan_uses_pinned_git_base_not_dirty_worktree(tmp_path: Path):
    repo, commit = _git_repo(tmp_path)
    (repo / "worker.py").write_text("value = 999\n", encoding="utf-8")
    expected = hashlib.sha256(b"value = 1\n").hexdigest()

    output, _ = run_execution_plan(
        _plan(commit, expected=expected),
        project_root=repo,
        staging_dir=repo / "staging",
        run_id="r1",
    )

    assert output.applied[0].original_content == "value = 1\n"
    assert "-value = 1" in (output.applied[0].diff or "")
    assert (repo / "staging" / "worker.py").read_text(encoding="utf-8") == "value = 2\n"


def test_checksum_conflict_requires_replan_and_cleans_staging(tmp_path: Path):
    repo, commit = _git_repo(tmp_path)
    plan = _plan(commit, expected="0" * 64)
    staging = repo / "staging"

    with pytest.raises(MutationPreconditionError) as raised:
        run_execution_plan(plan, project_root=repo, staging_dir=staging)

    assert raised.value.requires_replan is True
    assert _is_deterministic(raised.value)
    assert not staging.exists()


def test_duplicate_targets_are_aggregated_as_contract_violation(tmp_path: Path):
    repo, commit = _git_repo(tmp_path)
    plan = _plan(commit)
    plan.tasks.append(
        ExecutionTask(
            task_id="T2",
            operation=FileEditOperation(kind="file_edit", path="worker.py", content="value = 3\n"),
        )
    )

    with pytest.raises(ExecutionPlanContractError) as raised:
        validate_execution_plan(plan, repo)

    payload = raised.value.model_dump()
    assert payload["code"] == "execution_plan_contract_invalid"
    assert payload["category"] == "contract_violation"
    assert any(item["code"] == "duplicate_mutation_target" for item in payload["violations"])


def test_case_variant_targets_are_aggregated_as_contract_violation(tmp_path: Path):
    repo, commit = _git_repo(tmp_path)
    plan = _plan(commit, path="Worker.py")
    plan.tasks.append(
        ExecutionTask(
            task_id="T2",
            operation=FileEditOperation(kind="file_edit", path="worker.py", content="value = 3\n"),
        )
    )

    with pytest.raises(ExecutionPlanContractError) as raised:
        validate_execution_plan(plan, repo)

    assert any(
        item["code"] == "duplicate_mutation_target" and item["value"] == "worker.py"
        for item in raised.value.model_dump()["violations"]
    )


def test_noncanonical_target_is_rejected_before_staging(tmp_path: Path):
    repo, commit = _git_repo(tmp_path)
    plan = _plan(commit, path="src/../worker.py")

    with pytest.raises(ExecutionPlanContractError):
        run_execution_plan(plan, project_root=repo, staging_dir=repo / "staging")

    assert not (repo / "staging").exists()


def test_execution_plan_does_not_delete_nonempty_staging(tmp_path: Path):
    repo, commit = _git_repo(tmp_path)
    staging = repo / "staging"
    staging.mkdir()
    sentinel = staging / "unrelated.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(MutationPreconditionError):
        run_execution_plan(_plan(commit), project_root=repo, staging_dir=staging)

    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_missing_base_snapshot_requires_replan(tmp_path: Path):
    repo, _ = _git_repo(tmp_path)

    with pytest.raises(MutationPreconditionError) as raised:
        run_execution_plan(_plan("0" * 40), project_root=repo, staging_dir=repo / "staging")

    assert raised.value.requires_replan is True


def test_non_utf8_base_blob_requires_replan(tmp_path: Path):
    repo, _ = _git_repo(tmp_path)
    (repo / "worker.py").write_bytes(b"\xff")
    subprocess.run(["git", "add", "worker.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "binary base"], cwd=repo, check=True, capture_output=True
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    with pytest.raises(MutationPreconditionError, match="not UTF-8"):
        run_execution_plan(_plan(commit), project_root=repo, staging_dir=repo / "staging")


def test_legacy_analysis_only_task_is_rejected_before_provider_call(tmp_path: Path):
    repo, _ = _git_repo(tmp_path)
    proposal = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[
            Task(
                task_id="analysis",
                title="inspect worker",
                description="investigate checkpoint behavior",
                files_to_modify=[],
                priority="low",
                effort="low",
                risk_level="low",
            )
        ],
        blockers=[],
    )
    config = TargetConfig(target_path=repo, workspace_path=repo.parent / "workspace")

    with pytest.raises(ExecutionPlanContractError) as raised:
        run(proposal, config=config, staging_dir=repo / "staging")

    assert raised.value.model_dump()["violations"][0]["code"] == "analysis_only_task"
    assert not (repo / "staging").exists()
