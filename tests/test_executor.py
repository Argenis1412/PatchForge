import json
import logging
from unittest.mock import MagicMock

import pytest

from orchestrator.agents.executor import run
from orchestrator.agents.executor.providers import (
    _PROVIDER_CHAIN,
    KNOWN_PROVIDER_NAMES,
    ProviderChainResult,
    _call_chain,
    _provider_by_name,
)
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
    import orchestrator.agents.executor.logging as exec_logging

    exec_logging._logger = None
    for h in list(logging.getLogger("executor").handlers):
        logging.getLogger("executor").removeHandler(h)
        h.close()

    mock = MagicMock(wraps=exec_logging.get_file_logger)
    monkeypatch.setattr("orchestrator.agents.executor.logging.get_file_logger", mock)

    exec_logging._get_logger(tmp_path)
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

    # Mock _cb_gemini.call to return sequential content
    returns = [
        ("x = 2\n", 10, 5),
        ("x = 3\n", 10, 5),
    ]
    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: returns.pop(0)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    output, meta = run(arch_out, config=config, staging_dir=staging_dir)

    assert len(output.applied) == 2
    assert output.errors == []
    # Final staged file must contain the accumulated result
    staged_file = staging_dir / "test.py"
    assert staged_file.read_text(encoding="utf-8") == "x = 3\n"


@pytest.mark.unit
def test_rollback_to_commit_success(monkeypatch):
    from pathlib import Path

    from orchestrator.git import GitCommandResult

    def mock_force_reset(repo, sha):
        return GitCommandResult(return_code=0, stdout="", stderr="")

    monkeypatch.setattr("orchestrator.git.force_reset_apply", mock_force_reset)

    from orchestrator.agents.executor import rollback_to_commit

    rollback_to_commit(Path("/fake"), "abc123")


@pytest.mark.unit
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


@pytest.mark.unit
def test_apply_task_rejects_path_traversal(tmp_path):
    from orchestrator.agents.executor.applier import _apply_task
    from orchestrator.exceptions import PathSafetyError
    from orchestrator.schemas.architect_output import Task

    staging = tmp_path / "staging"
    staging.mkdir()

    task = Task(
        task_id="t1",
        title="traversal",
        description="attempt escape",
        files_to_modify=["../../evil.py"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    with pytest.raises(PathSafetyError) as exc_info:
        _apply_task(task, "run_test", tmp_path, staging)
    assert exc_info.value.path == "../../evil.py"
    assert exc_info.value.base == tmp_path


# ---------------------------------------------------------------------------
# Fix #2 — ProviderChainResult and failure tracking
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_provider_by_name_covers_all_chain_providers():
    by_name = _provider_by_name()
    all_chain_fns = {fn for chain in _PROVIDER_CHAIN.values() for fn in chain}
    for fn in all_chain_fns:
        short = fn.__name__.removeprefix("_call_")
        assert short in by_name, f"{short} missing from _provider_by_name()"
        assert by_name[short] is fn


@pytest.mark.unit
def test_known_provider_names_sorted():
    assert tuple(sorted(KNOWN_PROVIDER_NAMES)) == KNOWN_PROVIDER_NAMES
    assert len(KNOWN_PROVIDER_NAMES) >= 3


@pytest.mark.unit
def test_call_chain_all_providers_fail(monkeypatch):
    def _fail_a(prompt, run_id):
        raise Exception("a failed")

    def _fail_b(prompt, run_id):
        raise Exception("b failed")

    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._recoverable_exceptions",
        lambda: (Exception,),
    )
    result = _call_chain([_fail_a, _fail_b], "test prompt", "run_001")
    assert isinstance(result, ProviderChainResult)
    assert result.success is None
    assert len(result.failures) == 2
    assert result.failures[0] == ("_fail_a", "a failed")
    assert result.failures[1] == ("_fail_b", "b failed")


@pytest.mark.unit
def test_call_chain_success_preserves_partial_failures(monkeypatch):
    def _fail_first(prompt, run_id):
        raise Exception("first down")

    def _succeed(prompt, run_id):
        return ("patched code", 100, 50)

    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._recoverable_exceptions",
        lambda: (Exception,),
    )
    result = _call_chain([_fail_first, _succeed], "test prompt", "run_002")
    assert result.success is not None
    assert result.success[0] == "patched code"
    assert len(result.failures) == 1
    assert result.failures[0][0] == "_fail_first"


@pytest.mark.unit
def test_apply_task_error_contains_provider_names(tmp_path, monkeypatch):
    from orchestrator.agents.executor.applier import _apply_task

    source_file = tmp_path / "test.py"
    source_file.write_text("x = 1\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()

    task = Task(
        task_id="t1",
        title="change x",
        description="change x",
        files_to_modify=["test.py"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    def _always_fail(prompt, run_id):
        raise Exception("boom")

    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._recoverable_exceptions",
        lambda: (Exception,),
    )
    cb_mock = MagicMock()
    cb_mock.call.side_effect = lambda fn: (_ for _ in ()).throw(Exception("gemini boom"))
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_mock)

    cb_openrouter_mock = MagicMock()
    cb_openrouter_mock.call.side_effect = lambda fn: (_ for _ in ()).throw(
        Exception("openrouter boom")
    )
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_openrouter", cb_openrouter_mock)

    cb_claude_mock = MagicMock()
    cb_claude_mock.call.side_effect = lambda fn: (_ for _ in ()).throw(Exception("claude boom"))
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_claude", cb_claude_mock)

    change = _apply_task(task, "run_003", tmp_path, staging)
    assert change.status == "error"
    assert "_call_gemini" in change.error
    assert "_call_openrouter" in change.error
    assert "_call_claude" in change.error


# ---------------------------------------------------------------------------
# Fix #3 — force_provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_force_provider_overrides_chain(tmp_path, monkeypatch):
    from orchestrator.agents.executor.applier import _apply_task

    source_file = tmp_path / "test.py"
    source_file.write_text("x = 1\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()

    task = Task(
        task_id="t1",
        title="change x",
        description="change x to 2",
        files_to_modify=["test.py"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    cb_claude_mock = MagicMock()
    cb_claude_mock.call.side_effect = lambda fn: ("x = 2\n", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_claude", cb_claude_mock)

    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: (_ for _ in ()).throw(
        AssertionError("gemini should not be called")
    )
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    change = _apply_task(task, "run_004", tmp_path, staging, force_provider="claude")
    assert change.status == "applied"
    cb_claude_mock.call.assert_called_once()
    cb_gemini_mock.call.assert_not_called()


@pytest.mark.unit
def test_force_provider_preserves_high_risk_gating(tmp_path, monkeypatch):
    from orchestrator.agents.executor.applier import _apply_task

    source_file = tmp_path / "test.py"
    source_file.write_text("x = 1\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()

    task = Task(
        task_id="t1",
        title="change x",
        description="change x to 2",
        files_to_modify=["test.py"],
        priority="high",
        effort="low",
        risk_level="high",
        dependencies=[],
    )

    cb_claude_mock = MagicMock()
    cb_claude_mock.call.side_effect = lambda fn: ("x = 2\n", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_claude", cb_claude_mock)

    change = _apply_task(task, "run_005", tmp_path, staging, force_provider="claude")
    assert change.status == "pending_human_review"
    assert not (staging / "test.py").exists()


@pytest.mark.unit
def test_executor_run_unknown_force_provider(tmp_path):
    arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[],
        blockers=[],
    )
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)

    with pytest.raises(ValueError, match="Unknown provider.*pepino"):
        run(arch_out, config=config, force_provider="pepino")


# ---------------------------------------------------------------------------
# D-006 — Pre-diff syntax validation
# ---------------------------------------------------------------------------


class TestValidatePythonContent:
    @pytest.mark.unit
    def test_valid_python_returns_none(self):
        from orchestrator.agents.executor.validation import validate_python_content

        assert validate_python_content("x = 1\n", "x = 0\n", "test.py") is None

    @pytest.mark.unit
    def test_xml_markup_returns_error(self):
        from orchestrator.agents.executor.validation import validate_python_content

        result = validate_python_content(
            '<tool_call>read_file("a.py")</tool_call>',
            "x = 1\n",
            "scheduler.py",
        )
        assert result is not None
        assert "not valid Python" in result

    @pytest.mark.unit
    def test_both_broken_returns_none(self):
        from orchestrator.agents.executor.validation import validate_python_content

        result = validate_python_content("def f(\n", "def f(\n", "broken.py")
        assert result is None

    @pytest.mark.unit
    def test_markdown_preamble_returns_error(self):
        from orchestrator.agents.executor.validation import validate_python_content

        result = validate_python_content(
            "Here is the modified file:\n\nx = 1\n",
            "x = 1\n",
            "test.py",
        )
        assert result is not None
        assert "not valid Python" in result


@pytest.mark.unit
def test_apply_task_rejects_xml_output(tmp_path, monkeypatch):
    from orchestrator.agents.executor.applier import _apply_task

    source_file = tmp_path / "scheduler.py"
    source_file.write_text("x = 1\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()

    task = Task(
        task_id="t1",
        title="refactor",
        description="refactor scheduler",
        files_to_modify=["scheduler.py"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: (
        '<tool_call>read_file("a.py")</tool_call>',
        10,
        5,
    )
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    change = _apply_task(task, "run_d006", tmp_path, staging)
    assert change.status == "error"
    assert "not valid Python" in change.error
    assert change.tokens_used == 15
    assert not (staging / "scheduler.py").exists()


@pytest.mark.unit
def test_apply_task_valid_python_passes(tmp_path, monkeypatch):
    from orchestrator.agents.executor.applier import _apply_task

    source_file = tmp_path / "test.py"
    source_file.write_text("x = 1\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()

    task = Task(
        task_id="t1",
        title="bump x",
        description="change x to 2",
        files_to_modify=["test.py"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: ("x = 2\n", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    change = _apply_task(task, "run_d006", tmp_path, staging)
    assert change.status == "applied"


# ---------------------------------------------------------------------------
# Issue #208 — Executor lifecycle events
# ---------------------------------------------------------------------------


def _read_events(run_dir):
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        return []
    lines = events_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


@pytest.mark.unit
def test_executor_emits_lifecycle_events(tmp_path):
    arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[],
        blockers=[],
    )
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
    run_dir = tmp_path.parent / f"{tmp_path.name}-rundir"
    run_dir.mkdir()

    run(arch_out, config=config, run_dir=run_dir)

    events = [e["event"] for e in _read_events(run_dir)]
    assert "executor_start" in events
    assert "executor_end" in events
    assert events.index("executor_start") < events.index("executor_end")


@pytest.mark.unit
def test_executor_emits_task_events(tmp_path, monkeypatch):
    source_file = tmp_path / "test.py"
    source_file.write_text("x = 1\n", encoding="utf-8")

    task = Task(
        task_id="t1",
        title="bump x",
        description="change x to 2",
        files_to_modify=["test.py"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )
    arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[task],
        blockers=[],
    )
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
    staging_dir = tmp_path / "staging"
    run_dir = tmp_path.parent / f"{tmp_path.name}-rundir"
    run_dir.mkdir()

    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: ("x = 2\n", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    run(arch_out, config=config, staging_dir=staging_dir, run_dir=run_dir)

    events = [e["event"] for e in _read_events(run_dir)]
    assert "task_start" in events
    assert "file_start" in events
    assert "file_end" in events
    assert "task_end" in events


@pytest.mark.unit
def test_executor_emits_task_skipped(tmp_path, monkeypatch):
    source_file = tmp_path / "test.py"
    source_file.write_text("x = 1\n", encoding="utf-8")

    tasks = [
        Task(
            task_id="t1",
            title="fail",
            description="always errors",
            files_to_modify=["test.py"],
            priority="high",
            effort="low",
            risk_level="low",
            dependencies=[],
        ),
        Task(
            task_id="t2",
            title="depends on t1",
            description="should be skipped",
            files_to_modify=["test.py"],
            priority="high",
            effort="low",
            risk_level="low",
            dependencies=["t1"],
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
    run_dir = tmp_path.parent / f"{tmp_path.name}-rundir"
    run_dir.mkdir()

    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: (_ for _ in ()).throw(Exception("boom"))
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)
    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._recoverable_exceptions",
        lambda: (Exception,),
    )

    run(arch_out, config=config, staging_dir=staging_dir, run_dir=run_dir)

    events = _read_events(run_dir)
    skipped = [e for e in events if e["event"] == "task_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["data"]["task_id"] == "t2"
    assert skipped[0]["data"]["dependency"] == "t1"


@pytest.mark.unit
def test_executor_log_event_failure_does_not_crash(tmp_path, monkeypatch):
    arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[],
        blockers=[],
    )
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
    run_dir = tmp_path.parent / f"{tmp_path.name}-rundir"
    run_dir.mkdir()

    monkeypatch.setattr(
        "orchestrator.agents.executor.log_event",
        MagicMock(side_effect=OSError("disk full")),
    )

    output, meta = run(arch_out, config=config, run_dir=run_dir)
    assert isinstance(meta, dict)


@pytest.mark.unit
def test_executor_events_use_pipeline_trace_id_when_provided(tmp_path):
    """Events must carry the caller's trace_id, distinct from run_id, when given."""
    arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[],
        blockers=[],
    )
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
    run_dir = tmp_path.parent / f"{tmp_path.name}-rundir"
    run_dir.mkdir()

    run(arch_out, run_id="run-123", config=config, run_dir=run_dir, trace_id="trace-456")

    events = _read_events(run_dir)
    assert events
    for e in events:
        assert e["trace_id"] == "trace-456"
        assert e["run_id"] == "run-123"


@pytest.mark.unit
def test_apply_task_skips_validation_for_non_python(tmp_path, monkeypatch):
    from orchestrator.agents.executor.applier import _apply_task

    source_file = tmp_path / "config.toml"
    source_file.write_text("[tool]\nname = 'old'\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()

    task = Task(
        task_id="t1",
        title="update config",
        description="change name",
        files_to_modify=["config.toml"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: ("[tool]\nname = 'new'\n", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    change = _apply_task(task, "run_d006", tmp_path, staging)
    assert change.status == "applied"


# ---------------------------------------------------------------------------
# New-file creation support (Issue #210)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_apply_task_creates_new_file(tmp_path, monkeypatch):
    """A .py file that doesn't exist should be created, not rejected."""
    from orchestrator.agents.executor.applier import _apply_task

    staging = tmp_path / "staging"
    staging.mkdir()

    task = Task(
        task_id="t1",
        title="create test file",
        description="write a new test file",
        files_to_modify=["tests/test_new.py"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    new_content = "import pytest\n\ndef test_example():\n    assert True\n"
    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: (new_content, 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    change = _apply_task(task, "run_new", tmp_path, staging)
    assert change.status == "applied"
    assert (staging / "tests" / "test_new.py").exists()
    assert change.diff is not None
    assert "/dev/null" in change.diff


@pytest.mark.unit
def test_apply_task_creates_new_non_python_file(tmp_path, monkeypatch):
    """Non-Python new files skip syntax validation and succeed."""
    from orchestrator.agents.executor.applier import _apply_task

    staging = tmp_path / "staging"
    staging.mkdir()

    task = Task(
        task_id="t1",
        title="create readme",
        description="write a readme",
        files_to_modify=["docs/README.md"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    md_content = "# README\n\nHello world.\n"
    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: (md_content, 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    change = _apply_task(task, "run_md", tmp_path, staging)
    assert change.status == "applied"
    assert (staging / "docs" / "README.md").exists()
    assert "/dev/null" in change.diff


@pytest.mark.unit
def test_apply_task_new_file_accumulated_from_staging(tmp_path, monkeypatch):
    """A file not on disk but already in staging is a modification, not a creation."""
    from orchestrator.agents.executor.applier import _apply_task

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "new_module.py").write_text("x = 1\n", encoding="utf-8")

    task = Task(
        task_id="t2",
        title="extend module",
        description="add y",
        files_to_modify=["new_module.py"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: ("x = 1\ny = 2\n", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    change = _apply_task(task, "run_accum", tmp_path, staging)
    assert change.status == "applied"
    assert "/dev/null" not in change.diff
    assert "a/new_module.py" in change.diff


@pytest.mark.unit
def test_apply_task_new_file_rejects_path_traversal(tmp_path):
    """Path traversal is rejected even for files that don't exist."""
    from orchestrator.agents.executor.applier import _apply_task
    from orchestrator.exceptions import PathSafetyError

    staging = tmp_path / "staging"
    staging.mkdir()

    task = Task(
        task_id="t1",
        title="traversal",
        description="attempt escape",
        files_to_modify=["../../evil.py"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    with pytest.raises(PathSafetyError):
        _apply_task(task, "run_trav", tmp_path, staging)


@pytest.mark.unit
def test_new_file_diff_compatible_with_git_apply(tmp_path):
    """Diff generated for a new file must pass `git apply --check`."""
    import subprocess

    from orchestrator.agents.executor.diffing import _make_diff

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    new_content = "x = 1\ny = 2\n"
    diff = _make_diff("", new_content, "new_file.py", is_new_file=True)
    assert "/dev/null" in diff

    patch_path = tmp_path / "patch.diff"
    patch_path.write_text(diff, encoding="utf-8", newline="")

    res = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"git apply --check failed: {res.stderr}"
