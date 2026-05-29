"""Tests for pipeline correctness — status mapping, skip, failure propagation."""
from unittest.mock import MagicMock

import pytest
from orchestrator.pipeline import Pipeline
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.executor_output import ExecutorOutput, FileChange
from orchestrator.schemas.scout_output import ScoutOutput


@pytest.fixture
def config(tmp_path):
    return TargetConfig.load(target_path=tmp_path, workspace_path=tmp_path / "workspace")


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


def _meta(**overrides):
    m = {"tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0, "model_used": "test"}
    m.update(overrides)
    return m


def test_successful_run_completed(config, monkeypatch):
    monkeypatch.setattr("orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_architect", MagicMock(return_value=(_architect_output(tasks=[Task(task_id="t1", title="x", description="x", files_to_modify=["x.py"], priority="low", effort="low", risk_level="low", dependencies=[])]), _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_executor", MagicMock(return_value=(_executor_output(applied=1), _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_validator", MagicMock(return_value=(MagicMock(overall_passed=True), _meta())))

    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "completed"


def test_pending_review_produces_awaiting_review(config, monkeypatch):
    exec_out = ExecutorOutput(
        model="test", run_id="test",
        applied=[],
        pending_review=[FileChange(task_id="t1", file="x.py", status="pending_human_review", diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new")],
        errors=[],
    )
    monkeypatch.setattr("orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_architect", MagicMock(return_value=(_architect_output(tasks=[Task(task_id="t1", title="x", description="x", files_to_modify=["x.py"], priority="high", effort="low", risk_level="high", dependencies=[])]), _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_executor", MagicMock(return_value=(exec_out, _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_validator", MagicMock(return_value=(MagicMock(overall_passed=True), _meta())))

    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "awaiting_review"


def test_validator_failure_produces_validation_failed(config, monkeypatch):
    monkeypatch.setattr("orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_architect", MagicMock(return_value=(_architect_output(tasks=[Task(task_id="t1", title="x", description="x", files_to_modify=["x.py"], priority="low", effort="low", risk_level="low", dependencies=[])]), _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_executor", MagicMock(return_value=(_executor_output(applied=1), _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_validator", MagicMock(return_value=(MagicMock(overall_passed=False), _meta(model_used="test"))))

    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "validation_failed"


def test_validator_skip_does_not_crash(config, monkeypatch):
    monkeypatch.setattr("orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_architect", MagicMock(return_value=(_architect_output(), _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_executor", MagicMock(return_value=(_executor_output(), _meta())))

    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "completed"
    assert result.validator_meta is not None
    assert result.validator_meta.status == "skipped"
    assert result.validator_meta.latency_ms == 0


def test_architect_blockers_produce_failed(config, monkeypatch):
    monkeypatch.setattr("orchestrator.pipeline.run_scout", MagicMock(return_value=(_scout_output(), _meta())))
    monkeypatch.setattr("orchestrator.pipeline.run_architect", MagicMock(return_value=(_architect_output(blockers=["something blocked"]), _meta())))

    result = Pipeline(config=config).execute(dry_run=False)
    assert result.status == "failed"
