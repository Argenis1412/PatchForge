"""Tests for pipeline correctness — status mapping, skip, failure propagation."""

from unittest.mock import MagicMock

import pytest

from orchestrator.pipeline import Pipeline
from orchestrator.schemas.architect_output import ArchitectOutput, Task
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
