from unittest.mock import MagicMock

import pytest

from orchestrator.agents.executor import _build_dag, _topological_order, run
from orchestrator.exceptions import CycleDetectedError, SchedulerInvariantError
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.executor_output import FileChange, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(task_id: str, deps: list[str] | None = None) -> Task:
    return Task(
        task_id=task_id,
        title=f"task {task_id}",
        description="",
        files_to_modify=["dummy.py"],
        priority="medium",
        effort="low",
        risk_level="low",
        dependencies=deps or [],
    )


def _arch_out(tasks: list[Task]) -> ArchitectOutput:
    return ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=tasks,
        blockers=[],
    )


def _change(task_id: str, status: TaskStatus) -> FileChange:
    return FileChange(
        task_id=task_id,
        file="dummy.py",
        status=status,
        diff="",
        tokens_used=0,
        cost_usd=0.0,
    )


# ---------------------------------------------------------------------------
# _build_dag tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_dag_linear():
    tasks = [_make_task("A"), _make_task("B", ["A"]), _make_task("C", ["B"])]
    dag = _build_dag(tasks)
    assert dag == {"A": set(), "B": {"A"}, "C": {"B"}}


@pytest.mark.unit
def test_build_dag_missing_dependency():
    tasks = [_make_task("A", ["BOGUS"])]
    with pytest.raises(SchedulerInvariantError):
        _build_dag(tasks)


# ---------------------------------------------------------------------------
# _topological_order tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_topological_linear():
    tasks = [_make_task("A"), _make_task("B", ["A"]), _make_task("C", ["B"])]
    dag = _build_dag(tasks)
    ordered = _topological_order(tasks, dag)
    assert [t.task_id for t in ordered] == ["A", "B", "C"]


@pytest.mark.unit
def test_topological_diamond():
    tasks = [
        _make_task("A"),
        _make_task("B", ["A"]),
        _make_task("C", ["A"]),
        _make_task("D", ["B", "C"]),
    ]
    dag = _build_dag(tasks)
    ordered = _topological_order(tasks, dag)
    ids = [t.task_id for t in ordered]
    # A first, D last, B and C somewhere between
    assert ids[0] == "A"
    assert ids[-1] == "D"
    assert set(ids[1:-1]) == {"B", "C"}


@pytest.mark.unit
def test_topological_cycle():
    tasks = [_make_task("A", ["C"]), _make_task("B", ["A"]), _make_task("C", ["B"])]
    dag = _build_dag(tasks)
    with pytest.raises(CycleDetectedError):
        _topological_order(tasks, dag)


# ---------------------------------------------------------------------------
# Full scheduler: run() with mocked _apply_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_linear_dag(tmp_path, monkeypatch):
    """Test 1: Linear DAG A -> B -> C, all APPLIED."""
    monkeypatch.setattr(
        "orchestrator.agents.executor._apply_task",
        lambda task, *a, **kw: _change(task.task_id, TaskStatus.APPLIED),
    )
    tasks = [_make_task("A"), _make_task("B", ["A"]), _make_task("C", ["B"])]
    output, _ = run(_arch_out(tasks), config=_cfg(tmp_path))
    assert len(output.applied) == 3
    assert output.errors == []
    assert [c.task_id for c in output.applied] == ["A", "B", "C"]


@pytest.mark.unit
def test_diamond_dag(tmp_path, monkeypatch):
    """Test 2: Diamond A -> B, A -> C, B -> D, C -> D."""
    monkeypatch.setattr(
        "orchestrator.agents.executor._apply_task",
        lambda task, *a, **kw: _change(task.task_id, TaskStatus.APPLIED),
    )
    tasks = [
        _make_task("A"),
        _make_task("B", ["A"]),
        _make_task("C", ["A"]),
        _make_task("D", ["B", "C"]),
    ]
    output, _ = run(_arch_out(tasks), config=_cfg(tmp_path))
    assert len(output.applied) == 4
    assert output.errors == []


@pytest.mark.unit
def test_diamond_partial_failure(tmp_path, monkeypatch):
    """Test 3: Diamond, B=ERROR -> D=SKIPPED (even though C succeeds)."""
    results = {"A": TaskStatus.APPLIED, "B": TaskStatus.ERROR, "C": TaskStatus.APPLIED}

    def mock_apply(task, *a, **kw):
        return _change(task.task_id, results[task.task_id])

    monkeypatch.setattr("orchestrator.agents.executor._apply_task", mock_apply)
    tasks = [
        _make_task("A"),
        _make_task("B", ["A"]),
        _make_task("C", ["A"]),
        _make_task("D", ["B", "C"]),
    ]
    output, _ = run(_arch_out(tasks), config=_cfg(tmp_path))
    assert len(output.applied) == 2  # A, C
    assert len(output.errors) == 2  # B (ERROR), D (SKIPPED)
    # D must be SKIPPED
    d_change = next(c for c in output.errors if c.task_id == "D")
    assert d_change.status == TaskStatus.SKIPPED


@pytest.mark.unit
def test_cycle_detected(tmp_path, monkeypatch):
    """Test 4: Cycle A -> B -> C -> A -> CycleDetectedError."""
    monkeypatch.setattr(
        "orchestrator.agents.executor._apply_task",
        lambda task, *a, **kw: _change(task.task_id, TaskStatus.APPLIED),
    )
    tasks = [_make_task("A", ["C"]), _make_task("B", ["A"]), _make_task("C", ["B"])]
    with pytest.raises(CycleDetectedError):
        run(_arch_out(tasks), config=_cfg(tmp_path))


@pytest.mark.unit
def test_missing_dependency(tmp_path, monkeypatch):
    """Test 5: Missing dependency -> SchedulerInvariantError."""
    monkeypatch.setattr(
        "orchestrator.agents.executor._apply_task",
        lambda task, *a, **kw: _change(task.task_id, TaskStatus.APPLIED),
    )
    tasks = [_make_task("A", ["BOGUS"])]
    with pytest.raises(SchedulerInvariantError):
        run(_arch_out(tasks), config=_cfg(tmp_path))


@pytest.mark.unit
def test_noop_task(tmp_path, monkeypatch):
    """Test 6: No-op task produces NOOP status and diff=None."""
    monkeypatch.setattr(
        "orchestrator.agents.executor._apply_task",
        lambda task, *a, **kw: _change(task.task_id, TaskStatus.NOOP),
    )
    tasks = [_make_task("A")]
    output, _ = run(_arch_out(tasks), config=_cfg(tmp_path))
    assert len(output.applied) == 1
    assert output.applied[0].status == TaskStatus.NOOP


@pytest.mark.unit
def test_no_dependencies(tmp_path, monkeypatch):
    """Test 7: Single task with empty deps -> APPLIED normally."""
    monkeypatch.setattr(
        "orchestrator.agents.executor._apply_task",
        lambda task, *a, **kw: _change(task.task_id, TaskStatus.APPLIED),
    )
    tasks = [_make_task("A")]
    output, _ = run(_arch_out(tasks), config=_cfg(tmp_path))
    assert len(output.applied) == 1
    assert output.errors == []


@pytest.mark.unit
def test_mixed_states(tmp_path, monkeypatch):
    """Test 8: A -> B, A -> C. B=APPLIED, C=ERROR. B and C both processed."""
    results = {"A": TaskStatus.APPLIED, "B": TaskStatus.APPLIED, "C": TaskStatus.ERROR}

    def mock_apply(task, *a, **kw):
        return _change(task.task_id, results[task.task_id])

    monkeypatch.setattr("orchestrator.agents.executor._apply_task", mock_apply)
    tasks = [_make_task("A"), _make_task("B", ["A"]), _make_task("C", ["A"])]
    output, _ = run(_arch_out(tasks), config=_cfg(tmp_path))
    assert len(output.applied) == 2  # A, B
    assert len(output.errors) == 1  # C
    assert output.errors[0].task_id == "C"


@pytest.mark.unit
def test_pending_review_blocks_downstream(tmp_path, monkeypatch):
    """Test 9: A=PENDING_REVIEW -> B=SKIPPED."""
    results = {"A": TaskStatus.PENDING_REVIEW}

    def mock_apply(task, *a, **kw):
        return _change(task.task_id, results[task.task_id])

    monkeypatch.setattr("orchestrator.agents.executor._apply_task", mock_apply)
    tasks = [_make_task("A"), _make_task("B", ["A"])]
    output, _ = run(_arch_out(tasks), config=_cfg(tmp_path))
    # A in pending_review, B in errors (SKIPPED)
    assert len(output.pending_review) == 1
    assert len(output.errors) == 1
    assert output.errors[0].task_id == "B"
    assert output.errors[0].status == TaskStatus.SKIPPED
    assert "dependency A has status" in (output.errors[0].error or "")


@pytest.mark.unit
def test_multi_file_task_partial_error(tmp_path, monkeypatch):
    """Test 10: Multi-file task A with f1=ERROR, f2=APPLIED -> B depends on A -> B=SKIPPED."""
    file_results = {"f1.py": TaskStatus.ERROR, "f2.py": TaskStatus.APPLIED}

    def mock_apply(task, *a, **kw):
        f = task.files_to_modify[0]
        return _change(task.task_id, file_results[f])

    monkeypatch.setattr("orchestrator.agents.executor._apply_task", mock_apply)
    a = Task(
        task_id="A",
        title="multi-file",
        description="",
        files_to_modify=["f1.py", "f2.py"],
        priority="medium",
        effort="low",
        risk_level="low",
        dependencies=[],
    )
    b = _make_task("B", ["A"])
    output, _ = run(_arch_out([a, b]), config=_cfg(tmp_path))
    # A produces 1 error + 1 applied file change
    assert len(output.errors) == 2  # f1 ERROR + B SKIPPED
    assert len(output.applied) == 1  # f2 APPLIED
    b_change = next(c for c in output.errors if c.task_id == "B")
    assert b_change.status == TaskStatus.SKIPPED


@pytest.mark.unit
def test_long_cascade(tmp_path, monkeypatch):
    """Test 11: A(ERROR) -> B -> C -> D. All downstream SKIPPED."""
    results = {"A": TaskStatus.ERROR}

    def mock_apply(task, *a, **kw):
        return _change(task.task_id, results.get(task.task_id, TaskStatus.APPLIED))

    monkeypatch.setattr("orchestrator.agents.executor._apply_task", mock_apply)
    tasks = [
        _make_task("A"),
        _make_task("B", ["A"]),
        _make_task("C", ["B"]),
        _make_task("D", ["C"]),
    ]
    output, _ = run(_arch_out(tasks), config=_cfg(tmp_path))
    assert len(output.errors) == 4  # A ERROR + B, C, D SKIPPED
    assert len(output.applied) == 0
    skipped = [c for c in output.errors if c.status == TaskStatus.SKIPPED]
    assert len(skipped) == 3
    assert {c.task_id for c in skipped} == {"B", "C", "D"}


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------


def _cfg(tmp_path):
    return TargetConfig(target_path=tmp_path, workspace_path=tmp_path.parent / f"{tmp_path.name}-workspace")
