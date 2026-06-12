import logging
from unittest.mock import MagicMock

import pytest

from orchestrator.agents.executor import run
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.config import TargetConfig


@pytest.mark.unit
def test_executor_run_returns_tuple(mock_gemini, tmp_path):
    mock_gemini.return_value = {"applied": [], "errors": [], "pending_review": []}
    arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[],
        blockers=[],
    )
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
    output, meta = run(arch_out, config=config)
    assert isinstance(meta, dict)


@pytest.mark.unit
def test_executor_get_logger_uses_shared_helper(tmp_path, monkeypatch):
    import orchestrator.agents.executor as exec_mod

    exec_mod._logger = None
    for h in list(logging.getLogger("executor").handlers):
        logging.getLogger("executor").removeHandler(h)
        h.close()

    mock = MagicMock(wraps=exec_mod.get_file_logger)
    monkeypatch.setattr("orchestrator.agents.executor.get_file_logger", mock)

    exec_mod._get_logger(tmp_path)
    mock.assert_called_once_with("executor", tmp_path, "executor.log")


@pytest.mark.unit
def test_accumulated_changes_same_file(tmp_path, monkeypatch):
    """Two LOW tasks on the same file must accumulate, not overwrite."""
    # Create a source file with initial content
    source_file = tmp_path / "test.py"
    source_file.write_text("x = 1\n", encoding="utf-8")

    # Two tasks modifying the same file
    tasks = [
        Task(
            task_id="t1",
            title="change 1 to 2",
            description="bump x",
            files_to_modify=["test.py"],
            priority="high",
            effort="low",
            risk_level="low",
            dependencies=[],
        ),
        Task(
            task_id="t2",
            title="change 2 to 3",
            description="bump x again",
            files_to_modify=["test.py"],
            priority="high",
            effort="low",
            risk_level="low",
            dependencies=[],
        ),
    ]
    arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=tasks,
        blockers=[],
    )
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
    staging_dir = tmp_path / "staging"

    # Mock _call_gemini to return sequential content
    returns = [
        ("x = 2\n", 10, 5),
        ("x = 3\n", 10, 5),
    ]
    monkeypatch.setattr(
        "orchestrator.agents.executor._call_gemini",
        lambda *a, **kw: returns.pop(0),
    )

    output, meta = run(arch_out, config=config, staging_dir=staging_dir)

    assert len(output.applied) == 2
    assert output.errors == []
    # Final staged file must contain the accumulated result
    staged_file = staging_dir / "test.py"
    assert staged_file.read_text(encoding="utf-8") == "x = 3\n"


def test_rollback_to_commit_success(monkeypatch):
    from pathlib import Path

    from orchestrator.git import GitCommandResult

    def mock_force_reset(repo, sha):
        return GitCommandResult(return_code=0, stdout="", stderr="")

    monkeypatch.setattr("orchestrator.git.force_reset_apply", mock_force_reset)

    from orchestrator.agents.executor import rollback_to_commit

    rollback_to_commit(Path("/fake"), "abc123")


def test_rollback_to_commit_failure(monkeypatch):
    from pathlib import Path

    from orchestrator.exceptions import RollbackError
    from orchestrator.git import GitCommandResult

    def mock_force_reset(repo, sha):
        return GitCommandResult(return_code=1, stdout="", stderr="error detail")

    monkeypatch.setattr("orchestrator.git.force_reset_apply", mock_force_reset)

    from orchestrator.agents.executor import rollback_to_commit

    with pytest.raises(RollbackError) as exc_info:
        rollback_to_commit(Path("/fake"), "abc123")
    assert exc_info.value.repo_root == Path("/fake")
    assert exc_info.value.target_sha == "abc123"
    assert exc_info.value.stderr == "error detail"
