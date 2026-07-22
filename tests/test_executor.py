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
    _categorize_failure,
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


# ---------------------------------------------------------------------------
# D-011d Part 1 — primary_provider_attempted / primary_failure_category
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_call_chain_no_fallback_when_primary_succeeds(monkeypatch):
    def _call_succeed(prompt, run_id):
        return ("patched code", 100, 50)

    result = _call_chain([_call_succeed], "test prompt", "run_003")
    assert result.success is not None
    assert result.provider_name == result.primary_provider_attempted == "succeed"
    assert result.primary_failure_category is None


@pytest.mark.unit
def test_call_chain_reports_primary_provider_and_category_on_fallback(monkeypatch):
    """AC1/criterio único: provider_name != primary_provider_attempted signals
    a genuine fallback, with the category derived from the primary's own
    failure — not from whichever provider happened to fail last."""

    def _call_fail_primary(prompt, run_id):
        raise Exception("402 credit balance too low")

    def _call_succeed(prompt, run_id):
        return ("patched code", 100, 50)

    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._recoverable_exceptions",
        lambda: (Exception,),
    )
    result = _call_chain([_call_fail_primary, _call_succeed], "test prompt", "run_004")

    assert result.success is not None
    assert result.primary_provider_attempted == "fail_primary"
    assert result.provider_name == "succeed"
    assert result.provider_name != result.primary_provider_attempted
    assert result.primary_failure_category == "other"  # plain Exception, not a known API error type


@pytest.mark.unit
def test_call_chain_primary_provider_attempted_set_even_on_total_failure(monkeypatch):
    def _fail_a(prompt, run_id):
        raise Exception("a failed")

    def _fail_b(prompt, run_id):
        raise Exception("b failed")

    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._recoverable_exceptions",
        lambda: (Exception,),
    )
    result = _call_chain([_fail_a, _fail_b], "test prompt", "run_005")

    assert result.success is None
    assert result.primary_provider_attempted == "_fail_a"
    # failures keeps its original 2-tuple shape — regression guard for a
    # design mistake caught during planning that would have broken this
    # exact unpacking in agents/architect/provider.py and applier.py.
    for name, err in result.failures:
        assert isinstance(name, str)
        assert isinstance(err, str)


@pytest.mark.unit
def test_call_chain_empty_chain_has_no_primary():
    result = _call_chain([], "test prompt", "run_006")
    assert result.primary_provider_attempted is None
    assert result.primary_failure_category is None


@pytest.mark.unit
def test_categorize_failure_dispatches_by_type():
    from orchestrator.circuit_breaker import CircuitBreakerOpenError

    exc = CircuitBreakerOpenError("gemini", "OPEN", 30.0)
    assert _categorize_failure(exc) == "circuit_breaker_open"
    assert _categorize_failure(Exception("some random error")) == "other"


@pytest.mark.unit
def test_categorize_failure_credit_and_rate_limit_substrings():
    import anthropic

    credit_exc = anthropic.APIError("402 credit balance too low", request=MagicMock(), body=None)
    rate_exc = anthropic.APIError("429 rate limit exceeded", request=MagicMock(), body=None)
    other_exc = anthropic.APIError("500 internal error", request=MagicMock(), body=None)

    assert _categorize_failure(credit_exc) == "credit_exhausted"
    assert _categorize_failure(rate_exc) == "rate_limited"
    assert _categorize_failure(other_exc) == "other"


# ---------------------------------------------------------------------------
# D-011d Part 2 — executor-side fallback fields on FileChange +
# collect_fallback_changes filtering
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_apply_task_threads_fallback_fields_on_success(tmp_path, monkeypatch):
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

    import anthropic

    credit_exc = anthropic.APIError("402 credit balance too low", request=MagicMock(), body=None)

    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._recoverable_exceptions",
        lambda: (Exception,),
    )
    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: (_ for _ in ()).throw(credit_exc)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    cb_openrouter_mock = MagicMock()
    cb_openrouter_mock.call.side_effect = lambda fn: ("x = 2\n", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_openrouter", cb_openrouter_mock)

    change = _apply_task(task, "run_fb1", tmp_path, staging)

    assert change.status == "applied"
    assert change.provider_name == "openrouter"
    assert change.primary_provider_attempted == "gemini"
    assert change.primary_failure_category == "credit_exhausted"


@pytest.mark.unit
def test_apply_task_no_fallback_success_has_matching_primary(tmp_path, monkeypatch):
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

    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: ("x = 2\n", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    change = _apply_task(task, "run_fb2", tmp_path, staging)

    assert change.status == "applied"
    assert change.provider_name == change.primary_provider_attempted == "gemini"
    assert change.primary_failure_category is None


@pytest.mark.unit
def test_apply_task_threads_fallback_fields_on_noop(tmp_path, monkeypatch):
    """A fallback whose response is idempotent (no diff) must still thread
    the fallback fields onto the NOOP FileChange — a reachable combination
    (primary fails, fallback's response happens to match the original)."""
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

    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._recoverable_exceptions",
        lambda: (Exception,),
    )
    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: (_ for _ in ()).throw(
        Exception("circuit breaker open")
    )
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    cb_openrouter_mock = MagicMock()
    cb_openrouter_mock.call.side_effect = lambda fn: ("x = 1\n", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_openrouter", cb_openrouter_mock)

    change = _apply_task(task, "run_fb5", tmp_path, staging)

    assert change.status == "noop"
    assert change.provider_name == "openrouter"
    assert change.primary_provider_attempted == "gemini"
    assert change.primary_failure_category == "other"


@pytest.mark.unit
def test_apply_task_exhaustion_leaves_fallback_fields_none(tmp_path, monkeypatch):
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

    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._recoverable_exceptions",
        lambda: (Exception,),
    )
    for cb_name in ("_cb_gemini", "_cb_openrouter", "_cb_claude"):
        cb_mock = MagicMock()
        cb_mock.call.side_effect = lambda fn: (_ for _ in ()).throw(Exception("boom"))
        monkeypatch.setattr(f"orchestrator.agents.executor.providers.{cb_name}", cb_mock)

    change = _apply_task(task, "run_fb3", tmp_path, staging)

    assert change.status == "error"
    assert change.provider_name is None
    assert change.primary_provider_attempted is None
    assert change.primary_failure_category is None


@pytest.mark.unit
def test_apply_task_syntax_error_after_fallback_still_carries_diagnostic_fields(
    tmp_path, monkeypatch
):
    """A fallback provider's response can fail syntax validation and be
    discarded — the fields are still populated for diagnostics, but (see
    collect_fallback_changes tests below) must not trigger the warning."""
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

    import anthropic

    rate_exc = anthropic.APIError("429 rate limit exceeded", request=MagicMock(), body=None)

    monkeypatch.setattr(
        "orchestrator.agents.executor.providers._recoverable_exceptions",
        lambda: (Exception,),
    )
    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: (_ for _ in ()).throw(rate_exc)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    cb_openrouter_mock = MagicMock()
    cb_openrouter_mock.call.side_effect = lambda fn: ("<tool_call>not python</tool_call>", 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_openrouter", cb_openrouter_mock)

    change = _apply_task(task, "run_fb4", tmp_path, staging)

    assert change.status == "error"
    assert "not valid Python" in change.error
    assert change.provider_name == "openrouter"
    assert change.primary_provider_attempted == "gemini"
    assert change.primary_failure_category == "rate_limited"


@pytest.mark.unit
def test_apply_task_dependency_skip_has_no_fallback_fields():
    from orchestrator.schemas.executor_output import FileChange, TaskStatus

    change = FileChange(
        task_id="t1",
        file="test.py",
        status=TaskStatus.SKIPPED,
        error="dependency t0 has status TaskStatus.ERROR",
    )
    assert change.provider_name is None
    assert change.primary_provider_attempted is None
    assert change.primary_failure_category is None


# ---------------------------------------------------------------------------
# D-011d Part 2 — collect_fallback_changes
# ---------------------------------------------------------------------------


def _fc(**overrides):
    from orchestrator.schemas.executor_output import FileChange, TaskStatus

    defaults = {
        "task_id": "t1",
        "file": "a.py",
        "status": TaskStatus.APPLIED,
        "provider_name": "openrouter",
        "primary_provider_attempted": "gemini",
        "primary_failure_category": "credit_exhausted",
    }
    defaults.update(overrides)
    return FileChange(**defaults)


@pytest.mark.unit
def test_collect_fallback_changes_includes_genuine_fallback():
    from orchestrator.agents.executor.fallback import collect_fallback_changes
    from orchestrator.schemas.executor_output import ExecutorOutput

    change = _fc()
    result = ExecutorOutput(applied=[change])
    assert collect_fallback_changes(result) == [change]


@pytest.mark.unit
def test_collect_fallback_changes_excludes_no_fallback_success():
    from orchestrator.agents.executor.fallback import collect_fallback_changes
    from orchestrator.schemas.executor_output import ExecutorOutput

    change = _fc(provider_name="gemini", primary_provider_attempted="gemini")
    result = ExecutorOutput(applied=[change])
    assert collect_fallback_changes(result) == []


@pytest.mark.unit
def test_collect_fallback_changes_excludes_total_exhaustion():
    from orchestrator.agents.executor.fallback import collect_fallback_changes
    from orchestrator.schemas.executor_output import ExecutorOutput, TaskStatus

    change = _fc(
        status=TaskStatus.ERROR,
        provider_name=None,
        primary_provider_attempted=None,
        primary_failure_category=None,
        error="All providers failed for low-risk task: ...",
    )
    result = ExecutorOutput(errors=[change])
    assert collect_fallback_changes(result) == []


@pytest.mark.unit
def test_collect_fallback_changes_excludes_syntax_error_after_fallback():
    """The core round-2/round-3 adversarial fix: a fallback provider's
    response that failed syntax validation must not be reported as a
    successful fallback — nothing was actually delivered."""
    from orchestrator.agents.executor.fallback import collect_fallback_changes
    from orchestrator.schemas.executor_output import ExecutorOutput, TaskStatus

    change = _fc(status=TaskStatus.ERROR, error="not valid Python")
    result = ExecutorOutput(errors=[change])
    assert collect_fallback_changes(result) == []


@pytest.mark.unit
def test_collect_fallback_changes_excludes_dependency_skip():
    from orchestrator.agents.executor.fallback import collect_fallback_changes
    from orchestrator.schemas.executor_output import ExecutorOutput, TaskStatus

    change = _fc(
        status=TaskStatus.SKIPPED,
        provider_name=None,
        primary_provider_attempted=None,
        primary_failure_category=None,
        error="dependency t0 has status TaskStatus.ERROR",
    )
    result = ExecutorOutput(errors=[change])
    assert collect_fallback_changes(result) == []


@pytest.mark.unit
def test_collect_fallback_changes_includes_pending_review_fallback():
    from orchestrator.agents.executor.fallback import collect_fallback_changes
    from orchestrator.schemas.executor_output import ExecutorOutput, TaskStatus

    change = _fc(status=TaskStatus.PENDING_REVIEW)
    result = ExecutorOutput(pending_review=[change])
    assert collect_fallback_changes(result) == [change]


@pytest.mark.unit
def test_log_fallback_events_defaults_to_warning_level(tmp_path):
    """D-011d Part 3: the executor fallback event's severity was silently
    "info" (log_event's default) while the architect's equivalent event was
    "warning" — operationally backwards, since executor-stage fallback is
    more critical (silent code-quality degradation during patch generation)
    than architect-stage fallback (planning)."""
    import json

    from orchestrator.agents.executor.fallback import log_fallback_events

    change = _fc()
    logs_dir = tmp_path / "logs"
    log_fallback_events([change], run_id="r1", trace_id="r1", logs_dir=logs_dir, run_dir=None)

    lines = (logs_dir / "pipeline.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    fallback_events = [e for e in events if e.get("event") == "provider_fallback"]
    assert len(fallback_events) == 1
    assert fallback_events[0]["level"] == "warning"


# ---------------------------------------------------------------------------
# D-011d Part 2 — schema backward-compatibility
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_executor_output_parses_pre_part2_file_change_json():
    """A FileChange serialized before the two new fields existed (no
    primary_provider_attempted/primary_failure_category keys) must still
    deserialize, defaulting both to None."""
    from orchestrator.schemas.executor_output import ExecutorOutput

    pre_part2_json = json.dumps(
        {
            "applied": [
                {
                    "task_id": "t1",
                    "file": "a.py",
                    "status": "applied",
                    "provider_name": "gemini",
                }
            ],
            "pending_review": [],
            "errors": [],
            "total_tokens": 15,
            "total_cost_usd": 0.0,
            "model": "GM:gemini-2.5-flash|OR:openrouter/free|CL:claude-sonnet-4-6",
            "run_id": "run_old",
        }
    )
    result = ExecutorOutput.model_validate_json(pre_part2_json)
    assert result.applied[0].primary_provider_attempted is None
    assert result.applied[0].primary_failure_category is None


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
    def test_new_file_invalid_content_returns_error(self):
        """A literal empty-string original (the function's own new-file contract)
        must not mask invalid modified content — ast.parse("") does not raise."""
        from orchestrator.agents.executor.validation import validate_python_content

        result = validate_python_content("<tool_call>bad</tool_call>", "", "new_file.py")
        assert result is not None
        assert "not valid Python" in result

    @pytest.mark.unit
    def test_new_file_stand_in_invalid_content_returns_error(self):
        """The "# new file\\n" stand-in applier.py actually passes for new files
        must not mask invalid modified content either."""
        from orchestrator.agents.executor.validation import validate_python_content

        result = validate_python_content(
            "<tool_call>bad</tool_call>", "# new file\n", "new_file.py"
        )
        assert result is not None
        assert "not valid Python" in result


# ---------------------------------------------------------------------------
# Issue #245 — Fence-stripping fallback for LLM output
# ---------------------------------------------------------------------------


class TestStripFences:
    @pytest.mark.unit
    def test_no_fences(self):
        from orchestrator.agents.executor.validation import strip_fences

        assert strip_fences("x = 1\n") == "x = 1\n"

    @pytest.mark.unit
    def test_backtick_no_lang(self):
        from orchestrator.agents.executor.validation import strip_fences

        assert strip_fences("```\nx = 1\n```") == "x = 1"

    @pytest.mark.unit
    def test_backtick_with_lang(self):
        from orchestrator.agents.executor.validation import strip_fences

        assert strip_fences("```python\nx = 1\n```") == "x = 1"

    @pytest.mark.unit
    def test_backtick_with_lang_and_spaces(self):
        from orchestrator.agents.executor.validation import strip_fences

        assert strip_fences("``` python \nx = 1\n``` ") == "x = 1"

    @pytest.mark.unit
    def test_tilde(self):
        from orchestrator.agents.executor.validation import strip_fences

        assert strip_fences("~~~\nx = 1\n~~~") == "x = 1"

    @pytest.mark.unit
    def test_tilde_with_lang(self):
        from orchestrator.agents.executor.validation import strip_fences

        assert strip_fences("~~~python\nx = 1\n~~~") == "x = 1"

    @pytest.mark.unit
    def test_preamble_before_fence(self):
        from orchestrator.agents.executor.validation import strip_fences

        result = strip_fences("Here is the file:\n```python\nx = 1\n```")
        assert result == "x = 1"

    @pytest.mark.unit
    def test_trailing_after_fence(self):
        from orchestrator.agents.executor.validation import strip_fences

        result = strip_fences("```python\nx = 1\n```\nLet me know if you need anything else.")
        assert result == "x = 1"

    @pytest.mark.unit
    def test_inner_backticks_preserved(self):
        from orchestrator.agents.executor.validation import strip_fences

        content = '```python\ndef f():\n    """example: `x`"""\n    return 1\n```'
        result = strip_fences(content)
        assert result == 'def f():\n    """example: `x`"""\n    return 1'

    @pytest.mark.unit
    def test_only_opening_fence_no_strip(self):
        from orchestrator.agents.executor.validation import strip_fences

        content = "```python\nx = 1"
        assert strip_fences(content) == content

    @pytest.mark.unit
    def test_empty_content_between_fences(self):
        from orchestrator.agents.executor.validation import strip_fences

        assert strip_fences("```\n```") == ""

    @pytest.mark.unit
    def test_mismatched_fence_types_no_strip(self):
        from orchestrator.agents.executor.validation import strip_fences

        content = "```python\nx = 1\n~~~"
        assert strip_fences(content) == content

    @pytest.mark.unit
    def test_trailing_whitespace_on_fence_line(self):
        from orchestrator.agents.executor.validation import strip_fences

        assert strip_fences("```python   \nx = 1\n```   ") == "x = 1"

    @pytest.mark.unit
    def test_special_lang_tags(self):
        from orchestrator.agents.executor.validation import strip_fences

        assert strip_fences("```c++\nint x = 1;\n```") == "int x = 1;"
        assert strip_fences("```objective-c\nint x = 1;\n```") == "int x = 1;"
        assert strip_fences("```f#\nlet x = 1\n```") == "let x = 1"

    @pytest.mark.unit
    def test_multiple_blocks_no_strip(self):
        from orchestrator.agents.executor.validation import strip_fences

        content = "```python\nx = 1\n```\nSome text\n```python\ny = 2\n```"
        assert strip_fences(content) == content

    @pytest.mark.unit
    def test_multiline_real_file(self):
        from orchestrator.agents.executor.validation import strip_fences

        body = "import os\n\n\ndef main():\n    print(os.getcwd())\n"
        content = f"```python\n{body}```"
        assert strip_fences(content) == body.strip()


@pytest.mark.unit
def test_apply_task_skips_markdown_files(tmp_path, monkeypatch):
    from orchestrator.agents.executor.applier import _apply_task

    source_file = tmp_path / "README.md"
    source_file.write_text("# Title\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()

    fenced = "```\n# Title\n\nSome fenced content in the actual markdown.\n```"

    task = Task(
        task_id="t1",
        title="Update readme",
        description="desc",
        files_to_modify=["README.md"],
        priority="low",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: (fenced, 10, 10)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    result = _apply_task(task, "run1", tmp_path, staging)

    assert result.modified_content == fenced + "\n"


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

    cb_openrouter_mock = MagicMock()
    cb_openrouter_mock.call.side_effect = lambda fn: (_ for _ in ()).throw(Exception("boom"))
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_openrouter", cb_openrouter_mock)

    cb_claude_mock = MagicMock()
    cb_claude_mock.call.side_effect = lambda fn: (_ for _ in ()).throw(Exception("boom"))
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_claude", cb_claude_mock)

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
def test_apply_task_rejects_invalid_new_file(tmp_path, monkeypatch):
    """A new .py file with syntactically invalid LLM output must be rejected.

    Regression test for the stale "known limitation" claim (corrected in
    docs/context/discoveries.md): validate_python_content() is called with the
    "# new file\\n" stand-in for new files, which correctly does not mask
    invalid modified content.
    """
    from orchestrator.agents.executor.applier import _apply_task

    staging = tmp_path / "staging"
    staging.mkdir()

    task = Task(
        task_id="t1",
        title="create broken file",
        description="write a new file",
        files_to_modify=["broken_new.py"],
        priority="high",
        effort="low",
        risk_level="low",
        dependencies=[],
    )

    bad_content = "<tool_call>not python</tool_call>"
    cb_gemini_mock = MagicMock()
    cb_gemini_mock.call.side_effect = lambda fn: (bad_content, 10, 5)
    monkeypatch.setattr("orchestrator.agents.executor.providers._cb_gemini", cb_gemini_mock)

    change = _apply_task(task, "run_bad_new", tmp_path, staging)
    assert change.status == "error"
    assert not (staging / "broken_new.py").exists()


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
