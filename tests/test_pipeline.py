"""Tests for pipeline correctness — status mapping, skip, failure propagation."""

from unittest.mock import MagicMock

import pytest

from orchestrator.exceptions import SchemaVersionError
from orchestrator.pipeline import Pipeline
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.artifacts import CURRENT_SCHEMA_VERSION, RunMetadata
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.executor_output import ExecutorOutput, FileChange
from orchestrator.schemas.scout_output import ScoutOutput
from orchestrator.schemas.validator_output import ToolResult, ValidatorOutput


@pytest.fixture
def config(tmp_path):
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    return TargetConfig.load(target_path=tmp_path, workspace_path=workspace)


def _scout_output():
    return ScoutOutput(hotspots=[], recommended_order=[], risks=[], summary="test")


def _architect_output(tasks=None, blockers=None):
    return ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=tasks or [],
        blockers=blockers or [],
    )


def _executor_output(applied=0, pending=0):
    def _change(task_id, status):
        return FileChange(task_id=f"t{task_id}", file="x.py", status=status, diff="")

    return ExecutorOutput(
        model="test",
        run_id="test",
        applied=[_change(i, "applied") for i in range(applied)],
        pending_review=[_change(i, "pending_human_review") for i in range(pending)],
        errors=[],
    )


def _validator_output(passed=True):
    return ValidatorOutput(
        overall_passed=passed,
        tools=[ToolResult(tool="ruff", passed=True, return_code=0)],
        run_id="test",
        model_used_for_summary="",
    )


def _meta(**overrides):
    m = {"tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0, "model_used": "test"}
    m.update(overrides)
    return m


def _read_pipeline_events(logs_dir):
    import json as _json

    jsonl = logs_dir / "pipeline.jsonl"
    if not jsonl.exists():
        return []
    return [_json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line]


def test_successful_run_completed(config, monkeypatch):
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(
            return_value=(
                _architect_output(
                    tasks=[
                        Task(
                            task_id="t1",
                            title="x",
                            description="x",
                            files_to_modify=["x.py"],
                            priority="low",
                            effort="low",
                            risk_level="low",
                            dependencies=[],
                        )
                    ]
                ),
                _meta(),
            )
        ),
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor",
        MagicMock(return_value=(_executor_output(applied=1), _meta())),
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_validator",
        MagicMock(return_value=(_validator_output(passed=True), _meta())),
    )

    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "completed"


def test_stage_executor_forwards_pipeline_trace_id(config, monkeypatch):
    """The executor must receive the pipeline's trace_id, not just run_id,
    so its events correlate with the rest of the run's trace."""
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(
            return_value=(
                _architect_output(
                    tasks=[
                        Task(
                            task_id="t1",
                            title="x",
                            description="x",
                            files_to_modify=["x.py"],
                            priority="low",
                            effort="low",
                            risk_level="low",
                            dependencies=[],
                        )
                    ]
                ),
                _meta(),
            )
        ),
    )
    mock_run_executor = MagicMock(return_value=(_executor_output(applied=1), _meta()))
    monkeypatch.setattr("orchestrator.pipeline.run_executor", mock_run_executor)
    monkeypatch.setattr(
        "orchestrator.pipeline.run_validator",
        MagicMock(return_value=(_validator_output(passed=True), _meta())),
    )

    pipeline = Pipeline(config=config)
    pipeline.execute(dry_run=False)

    assert mock_run_executor.call_args.kwargs["trace_id"] == pipeline.trace_id
    assert mock_run_executor.call_args.kwargs["run_id"] == pipeline.run.run_id


def test_pending_review_produces_awaiting_review(config, monkeypatch):
    exec_out = ExecutorOutput(
        model="test",
        run_id="test",
        applied=[],
        pending_review=[
            FileChange(
                task_id="t1",
                file="x.py",
                status="pending_human_review",
                diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new",
            )
        ],
        errors=[],
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(
            return_value=(
                _architect_output(
                    tasks=[
                        Task(
                            task_id="t1",
                            title="x",
                            description="x",
                            files_to_modify=["x.py"],
                            priority="high",
                            effort="low",
                            risk_level="high",
                            dependencies=[],
                        )
                    ]
                ),
                _meta(),
            )
        ),
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor", MagicMock(return_value=(exec_out, _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_validator",
        MagicMock(return_value=(_validator_output(passed=True), _meta())),
    )

    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "awaiting_review"


def test_validator_failure_produces_validation_failed(config, monkeypatch):
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(
            return_value=(
                _architect_output(
                    tasks=[
                        Task(
                            task_id="t1",
                            title="x",
                            description="x",
                            files_to_modify=["x.py"],
                            priority="low",
                            effort="low",
                            risk_level="low",
                            dependencies=[],
                        )
                    ]
                ),
                _meta(),
            )
        ),
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor",
        MagicMock(return_value=(_executor_output(applied=1), _meta())),
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_validator",
        MagicMock(return_value=(_validator_output(passed=False), _meta(model_used="test"))),
    )

    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "validation_failed"


def test_validator_skip_does_not_crash(config, monkeypatch):
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(return_value=(_architect_output(), _meta())),
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor", MagicMock(return_value=(_executor_output(), _meta()))
    )

    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "completed"
    assert result.validator_meta is not None
    assert result.validator_meta.status == "skipped"
    assert result.validator_meta.latency_ms == 0


def test_architect_blockers_produce_failed(config, monkeypatch):
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(return_value=(_architect_output(blockers=["something blocked"]), _meta())),
    )

    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "failed"


def test_resume_from_scout_output(config, monkeypatch):
    pipeline = Pipeline(config=config)
    path = pipeline.workspace.outputs / f"scout_{pipeline.run.run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_scout_output().model_dump_json())
    pipeline.workspace.update_manifest("scout", f"scout_{pipeline.run.run_id}.json")

    mock_scout = MagicMock()
    monkeypatch.setattr("orchestrator.pipeline.run_scout", mock_scout)
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(return_value=(_architect_output(), _meta())),
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor", MagicMock(return_value=(_executor_output(), _meta()))
    )

    result = Pipeline(config=config, from_stage="scout").execute(dry_run=False)
    assert result.status == "completed"
    mock_scout.assert_not_called()


def test_resume_from_architect_output(config, monkeypatch):
    pipeline = Pipeline(config=config)
    path = pipeline.workspace.outputs / f"architect_{pipeline.run.run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_architect_output().model_dump_json())
    pipeline.workspace.update_manifest("architect", f"architect_{pipeline.run.run_id}.json")

    mock_scout = MagicMock()
    mock_architect = MagicMock()
    monkeypatch.setattr("orchestrator.pipeline.run_scout", mock_scout)
    monkeypatch.setattr("orchestrator.pipeline.run_architect", mock_architect)
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor", MagicMock(return_value=(_executor_output(), _meta()))
    )

    result = Pipeline(config=config, from_stage="architect").execute(dry_run=False)
    assert result.status == "completed"
    mock_scout.assert_not_called()
    mock_architect.assert_not_called()


def test_resume_from_executor_reloads_task_count(config, monkeypatch):
    pipeline = Pipeline(config=config)
    path = pipeline.workspace.outputs / f"executor_{pipeline.run.run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_executor_output(applied=2).model_dump_json())
    pipeline.workspace.update_manifest("executor", f"executor_{pipeline.run.run_id}.json")

    mock_validator = MagicMock(return_value=(_validator_output(passed=True), _meta()))
    monkeypatch.setattr("orchestrator.pipeline.run_scout", MagicMock())
    monkeypatch.setattr("orchestrator.pipeline.run_architect", MagicMock())
    monkeypatch.setattr("orchestrator.pipeline.run_executor", MagicMock())
    monkeypatch.setattr("orchestrator.pipeline.run_validator", mock_validator)

    result = Pipeline(config=config, from_stage="executor").execute(dry_run=False)
    assert result.status == "completed"
    assert result.tasks_applied == 2
    assert result.validator_meta is not None
    assert result.validator_meta.status == "success"


def test_failure_artifacts_populated_on_pipeline_abort(config, monkeypatch):
    from datetime import datetime, timezone

    from orchestrator.schemas.artifacts import RunMetadata

    pipeline = Pipeline(config=config)
    # Write RunMetadata so the pipeline can populate failure_artifacts
    run_meta = RunMetadata(
        run_id=pipeline.run.run_id,
        target_path=str(pipeline.target_path),
        workspace_path=str(pipeline.workspace.root),
        base_commit="abc123",
        branch="main",
        status="scanning",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        v1_supported=True,
        support_reasons=["test"],
    )
    pipeline.workspace.create_run_directory(pipeline.run.run_id)
    pipeline.workspace.write_run_json(pipeline.run.run_id, run_meta)

    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    # Architect raises blockers → triggers PipelineAbort
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(return_value=(_architect_output(blockers=["blocker"]), _meta())),
    )

    result = pipeline.execute(dry_run=False)
    assert result.status == "failed"

    # Verify failure_artifacts was populated on RunMetadata
    updated_meta = pipeline.workspace.read_run_json(pipeline.run.run_id)
    assert updated_meta.failure_artifacts is not None
    assert len(updated_meta.failure_artifacts) > 0
    assert "architect_failure.json" in updated_meta.failure_artifacts
    assert updated_meta.status == "failed"


def _run_metadata(schema_version: int = CURRENT_SCHEMA_VERSION) -> RunMetadata:
    from datetime import datetime, timezone

    return RunMetadata(
        run_id="test_guard",
        target_path="/tmp/test",
        workspace_path="/tmp/ws",
        base_commit="abc123",
        branch="main",
        status="scanning",
        schema_version=schema_version,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        v1_supported=True,
        support_reasons=["test"],
    )


def test_guard_no_existing_artifact(config, monkeypatch):
    """No RunMetadata in workspace → guard passes silently."""
    monkeypatch.setattr(
        "orchestrator.pipeline.WorkspaceManager.read_run_json",
        MagicMock(side_effect=FileNotFoundError),
    )
    pipeline = Pipeline(config=config)
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(return_value=(_architect_output(), _meta())),
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor", MagicMock(return_value=(_executor_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_validator",
        MagicMock(return_value=(_validator_output(passed=True), _meta())),
    )
    result = pipeline.execute(dry_run=False)
    assert result.status == "completed"


def test_guard_valid_version(config, monkeypatch):
    """schema_version matches CURRENT_SCHEMA_VERSION → no error."""
    mock_read = MagicMock(return_value=_run_metadata(schema_version=CURRENT_SCHEMA_VERSION))
    monkeypatch.setattr("orchestrator.pipeline.WorkspaceManager.read_run_json", mock_read)
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(return_value=(_architect_output(), _meta())),
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor", MagicMock(return_value=(_executor_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_validator",
        MagicMock(return_value=(_validator_output(passed=True), _meta())),
    )
    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "completed"


def test_guard_future_version(config, monkeypatch):
    """schema_version > CURRENT_SCHEMA_VERSION → SchemaVersionError."""
    mock_read = MagicMock(return_value=_run_metadata(schema_version=2))
    monkeypatch.setattr("orchestrator.pipeline.WorkspaceManager.read_run_json", mock_read)
    mock_scout = MagicMock(return_value=(_scout_output(), _meta()))
    mock_architect = MagicMock(return_value=(_architect_output(), _meta()))
    monkeypatch.setattr("orchestrator.pipeline.run_scout", mock_scout)
    monkeypatch.setattr("orchestrator.pipeline.run_architect", mock_architect)
    with pytest.raises(SchemaVersionError) as exc_info:
        Pipeline(config=config).execute(dry_run=False)
    mock_scout.assert_not_called()
    mock_architect.assert_not_called()
    assert exc_info.value.found == 2
    assert exc_info.value.expected == CURRENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# D-011d Part 2 — executor-side provider fallback warning in pipeline.py
# ---------------------------------------------------------------------------


def _fallback_change(task_id="t1"):
    return FileChange(
        task_id=task_id,
        file="x.py",
        status="applied",
        diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new",
        provider_name="openrouter",
        primary_provider_attempted="gemini",
        primary_failure_category="credit_exhausted",
    )


def _no_fallback_change(task_id="t1", provider="gemini"):
    return FileChange(
        task_id=task_id,
        file="x.py",
        status="applied",
        diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new",
        provider_name=provider,
        primary_provider_attempted=provider,
        primary_failure_category=None,
    )


def test_stage_executor_emits_fallback_event(config, monkeypatch):
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(return_value=(_architect_output(), _meta())),
    )
    exec_out = ExecutorOutput(model="test", run_id="test", applied=[_fallback_change()], errors=[])
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor", MagicMock(return_value=(exec_out, _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_validator",
        MagicMock(return_value=(_validator_output(passed=True), _meta())),
    )

    pipeline = Pipeline(config=config)
    result = pipeline.execute(dry_run=False)
    assert result.status == "completed"

    events = _read_pipeline_events(config.workspace_path / "logs")
    fallback_events = [e for e in events if e.get("event") == "provider_fallback"]
    assert len(fallback_events) == 1
    ev = fallback_events[0]
    assert ev["stage"] == "executor"
    assert ev["trace_id"] == pipeline.trace_id
    assert ev["data"]["primary_provider"] == "gemini"
    assert ev["data"]["used_provider"] == "openrouter"
    assert ev["data"]["category"] == "credit_exhausted"


def test_stage_executor_no_fallback_emits_no_event(config, monkeypatch):
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(return_value=(_architect_output(), _meta())),
    )
    exec_out = ExecutorOutput(
        model="test", run_id="test", applied=[_no_fallback_change()], errors=[]
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor", MagicMock(return_value=(exec_out, _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_validator",
        MagicMock(return_value=(_validator_output(passed=True), _meta())),
    )

    pipeline = Pipeline(config=config)
    result = pipeline.execute(dry_run=False)
    assert result.status == "completed"

    events = _read_pipeline_events(config.workspace_path / "logs")
    fallback_events = [e for e in events if e.get("event") == "provider_fallback"]
    assert fallback_events == []


def test_stage_executor_persist_failure_emits_no_fallback_event(config, monkeypatch):
    """If persisting the executor's output fails, no provider_fallback event
    should have been logged for a stage whose output isn't durably saved."""
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(return_value=(_architect_output(), _meta())),
    )
    exec_out = ExecutorOutput(model="test", run_id="test", applied=[_fallback_change()], errors=[])
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor", MagicMock(return_value=(exec_out, _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.Pipeline._persist_stage_output",
        MagicMock(side_effect=OSError("disk full")),
    )

    pipeline = Pipeline(config=config)
    result = pipeline.execute(dry_run=False)
    assert result.status == "failed"

    events = _read_pipeline_events(config.workspace_path / "logs")
    fallback_events = [e for e in events if e.get("event") == "provider_fallback"]
    assert fallback_events == []


def test_resume_from_executor_does_not_reemit_fallback_event(config, monkeypatch):
    """The event for any real fallback was already emitted once, during the
    original fresh execution that produced the persisted file — resuming
    must not duplicate it under a new trace_id."""
    pipeline = Pipeline(config=config)
    exec_out = ExecutorOutput(model="test", run_id="test", applied=[_fallback_change()], errors=[])
    path = pipeline.workspace.outputs / f"executor_{pipeline.run.run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(exec_out.model_dump_json())
    pipeline.workspace.update_manifest("executor", f"executor_{pipeline.run.run_id}.json")

    monkeypatch.setattr("orchestrator.pipeline.run_scout", MagicMock())
    monkeypatch.setattr("orchestrator.pipeline.run_architect", MagicMock())
    monkeypatch.setattr("orchestrator.pipeline.run_executor", MagicMock())
    monkeypatch.setattr(
        "orchestrator.pipeline.run_validator",
        MagicMock(return_value=(_validator_output(passed=True), _meta())),
    )

    result = Pipeline(config=config, from_stage="executor").execute(dry_run=False)
    assert result.status == "completed"
    assert result.tasks_applied == 1

    events = _read_pipeline_events(config.workspace_path / "logs")
    fallback_events = [e for e in events if e.get("event") == "provider_fallback"]
    assert fallback_events == []


def test_task_result_model_used_reflects_actual_provider(config, monkeypatch):
    """TaskResult.model_used must be the real per-task provider (or the
    "n/a" sentinel), never the run-level aggregate config string."""
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(return_value=(_architect_output(), _meta())),
    )
    skipped_change = FileChange(
        task_id="t2",
        file="y.py",
        status="skipped",
        error="dependency t0 has status TaskStatus.ERROR",
    )
    exec_out = ExecutorOutput(
        model="GM:gemini-2.5-flash|OR:openrouter/free|CL:claude-sonnet-4-6",
        run_id="test",
        applied=[_fallback_change(task_id="t1")],
        errors=[skipped_change],
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor", MagicMock(return_value=(exec_out, _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_validator",
        MagicMock(return_value=(_validator_output(passed=True), _meta())),
    )

    result = Pipeline(config=config).execute(dry_run=False)

    by_task = {tr.task_id: tr.model_used for tr in result.task_results}
    assert by_task["t1"] == "openrouter"
    assert by_task["t2"] == "n/a"
    assert "GM:" not in by_task["t1"]
    assert "GM:" not in by_task["t2"]


def test_stage_executor_tolerates_fallback_logging_failure(config, monkeypatch):
    """A logging failure inside log_fallback_events must not crash an
    otherwise-successful executor stage."""
    monkeypatch.setattr(
        "orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_architect",
        MagicMock(return_value=(_architect_output(), _meta())),
    )
    exec_out = ExecutorOutput(model="test", run_id="test", applied=[_fallback_change()], errors=[])
    monkeypatch.setattr(
        "orchestrator.pipeline.run_executor", MagicMock(return_value=(exec_out, _meta()))
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.run_validator",
        MagicMock(return_value=(_validator_output(passed=True), _meta())),
    )
    monkeypatch.setattr(
        "orchestrator.agents.executor.fallback.log_event",
        MagicMock(side_effect=OSError("disk full")),
    )

    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "completed"
    assert result.tasks_applied == 1


def test_guard_past_version(config, monkeypatch):
    """schema_version < CURRENT_SCHEMA_VERSION → SchemaVersionError."""
    mock_read = MagicMock(return_value=_run_metadata(schema_version=0))
    monkeypatch.setattr("orchestrator.pipeline.WorkspaceManager.read_run_json", mock_read)
    mock_scout = MagicMock(return_value=(_scout_output(), _meta()))
    mock_architect = MagicMock(return_value=(_architect_output(), _meta()))
    monkeypatch.setattr("orchestrator.pipeline.run_scout", mock_scout)
    monkeypatch.setattr("orchestrator.pipeline.run_architect", mock_architect)
    with pytest.raises(SchemaVersionError) as exc_info:
        Pipeline(config=config).execute(dry_run=False)
    mock_scout.assert_not_called()
    mock_architect.assert_not_called()
    assert exc_info.value.found == 0
    assert exc_info.value.expected == CURRENT_SCHEMA_VERSION
