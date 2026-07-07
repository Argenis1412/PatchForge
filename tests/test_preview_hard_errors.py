"""Tests for D-003: executor hard errors force validation_failed status."""

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock

import pytest

from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.schemas.executor_output import (
    ExecutorOutput,
    FileChange,
    TaskStatus,
)
from orchestrator.schemas.git import GitCommandResult
from orchestrator.schemas.risk import RiskGateResult
from orchestrator.schemas.validator_output import ValidatorOutput
from orchestrator.workspace import WorkspaceManager


def _init_git_repo(path: Path) -> str:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "file.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _get_branch(path: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _make_plan() -> ArchitectOutput:
    return ArchitectOutput(
        validated_findings=["Finding 1"],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[
            Task(
                task_id="T1",
                title="Fix thing",
                description="Fix",
                files_to_modify=["file.txt"],
                priority="medium",
                effort="low",
                risk_level="low",
            )
        ],
        blockers=[],
    )


@contextmanager
def _mock_validation_workspace() -> Generator:
    ws = MagicMock()
    ws.temporary_root = Path("/tmp/fake-val-ws")
    ws.patch_path = Path("/tmp/fake-val-ws/patch.diff")
    yield ws


def _execute(*args, **kwargs):
    from orchestrator.commands.preview import execute

    return execute(*args, **kwargs)


@pytest.fixture(autouse=True)
def _unload_preview_module():
    import sys

    yield
    sys.modules.pop("orchestrator.commands.preview", None)


@pytest.fixture()
def env(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()
    head_sha = _init_git_repo(target)
    branch = _get_branch(target)

    workspace_path = tmp_path / "workspace"
    wm = WorkspaceManager(workspace_path)
    wm.setup()

    run_id = "test-hard-errors"
    wm.create_run_directory(run_id)

    meta = RunMetadata(
        run_id=run_id,
        target_path=str(target),
        workspace_path=str(workspace_path),
        base_commit=head_sha,
        branch=branch,
        status="planned",
        v1_supported=True,
        max_files=10,
        max_diff_lines=500,
    )
    wm.write_run_json(run_id, meta)
    wm.write_artifact(run_id, "plan.json", _make_plan().model_dump_json(indent=2))

    return {
        "target": target,
        "workspace_path": workspace_path,
        "wm": wm,
        "run_id": run_id,
    }


DIFF_TEXT = "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-hello\n+world"


def _patch_common(monkeypatch, executor_output, validator_output):
    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)
    monkeypatch.setattr(
        "orchestrator.agents.executor.run",
        MagicMock(return_value=(executor_output, {"cost_usd": 0.01})),
    )
    monkeypatch.setattr(
        "orchestrator.risk.check_patch_gate",
        MagicMock(return_value=RiskGateResult(passed=True, gate="size", reasons=[])),
    )
    monkeypatch.setattr(
        "orchestrator.validation_workspace.create_validation_workspace",
        MagicMock(return_value=_mock_validation_workspace()),
    )
    monkeypatch.setattr(
        "orchestrator.validation_workspace.apply_patch_to_copy",
        MagicMock(return_value=GitCommandResult(return_code=0, stdout="ok", stderr="")),
    )
    monkeypatch.setattr(
        "orchestrator.validation_workspace.run_validation_in_copy",
        MagicMock(return_value=validator_output),
    )


class TestHardErrorsForcesFailure:
    def test_error_task_with_validator_pass_marks_validation_failed(self, env, monkeypatch):
        executor_output = ExecutorOutput(
            applied=[
                FileChange(task_id="T1", file="file.txt", status=TaskStatus.APPLIED, diff=DIFF_TEXT)
            ],
            errors=[
                FileChange(
                    task_id="T2",
                    file="tests/test_risk.py",
                    status=TaskStatus.ERROR,
                    error="File not found",
                )
            ],
        )
        validator_output = ValidatorOutput(
            overall_passed=True,
            tools=[],
            run_id=env["run_id"],
        )
        _patch_common(monkeypatch, executor_output, validator_output)

        _execute(env["run_id"], workspace=env["workspace_path"])

        run_dir = env["wm"].run_dir(env["run_id"])
        run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert run_json["status"] == "validation_failed"
        assert run_json["executor_had_errors"] is True
        assert "Incomplete deliverables" in run_json["validation_summary"]
        assert "T2" in run_json["validation_summary"]

    def test_skipped_task_does_not_invalidate(self, env, monkeypatch):
        executor_output = ExecutorOutput(
            applied=[
                FileChange(task_id="T1", file="file.txt", status=TaskStatus.APPLIED, diff=DIFF_TEXT)
            ],
            errors=[
                FileChange(
                    task_id="T2",
                    file="file2.txt",
                    status=TaskStatus.SKIPPED,
                    error="Dependency chain",
                )
            ],
        )
        validator_output = ValidatorOutput(
            overall_passed=True,
            tools=[],
            run_id=env["run_id"],
        )
        _patch_common(monkeypatch, executor_output, validator_output)

        _execute(env["run_id"], workspace=env["workspace_path"])

        run_dir = env["wm"].run_dir(env["run_id"])
        run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert run_json["status"] == "previewed"
        assert run_json["executor_had_errors"] is False


class TestValidationSummaryComposition:
    def test_hard_errors_precede_timeout(self, env, monkeypatch):
        from orchestrator.schemas.validator_output import ToolResult

        executor_output = ExecutorOutput(
            applied=[
                FileChange(task_id="T1", file="file.txt", status=TaskStatus.APPLIED, diff=DIFF_TEXT)
            ],
            errors=[
                FileChange(
                    task_id="T2",
                    file="ghost.py",
                    status=TaskStatus.ERROR,
                    error="File not found",
                )
            ],
        )
        validator_output = ValidatorOutput(
            overall_passed=True,
            tools=[ToolResult(tool="pytest", passed=True, timed_out=True, return_code=0)],
            run_id=env["run_id"],
        )
        _patch_common(monkeypatch, executor_output, validator_output)

        _execute(env["run_id"], workspace=env["workspace_path"])

        run_dir = env["wm"].run_dir(env["run_id"])
        run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        summary = run_json["validation_summary"]
        incomplete_pos = summary.index("Incomplete deliverables")
        timeout_pos = summary.index("Timeout")
        assert incomplete_pos < timeout_pos

    def test_llm_summary_none_fallback(self, env, monkeypatch):
        executor_output = ExecutorOutput(
            applied=[
                FileChange(task_id="T1", file="file.txt", status=TaskStatus.APPLIED, diff=DIFF_TEXT)
            ],
            errors=[
                FileChange(
                    task_id="T2",
                    file="ghost.py",
                    status=TaskStatus.ERROR,
                    error="File not found",
                )
            ],
        )
        validator_output = ValidatorOutput(
            overall_passed=True,
            tools=[],
            run_id=env["run_id"],
            llm_summary=None,
        )
        _patch_common(monkeypatch, executor_output, validator_output)

        _execute(env["run_id"], workspace=env["workspace_path"])

        run_dir = env["wm"].run_dir(env["run_id"])
        run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert run_json["validation_summary"].endswith("Validation failed")


class TestPanelCoherence:
    def test_hard_errors_show_failure_panel(self, env, monkeypatch, capsys):
        executor_output = ExecutorOutput(
            applied=[
                FileChange(task_id="T1", file="file.txt", status=TaskStatus.APPLIED, diff=DIFF_TEXT)
            ],
            errors=[
                FileChange(
                    task_id="T2",
                    file="ghost.py",
                    status=TaskStatus.ERROR,
                    error="File not found",
                )
            ],
        )
        validator_output = ValidatorOutput(
            overall_passed=True,
            tools=[],
            run_id=env["run_id"],
        )
        _patch_common(monkeypatch, executor_output, validator_output)

        _execute(env["run_id"], workspace=env["workspace_path"])

        captured = capsys.readouterr().out
        assert "completed successfully" not in captured
        assert "FAILED" in captured
