"""Tests for auto_apply_eligible field on RunMetadata."""

from __future__ import annotations

import pytest

from orchestrator.schemas.artifacts import PatchLifecycleState, RunMetadata


def _make_run_metadata(**kwargs) -> RunMetadata:
    defaults = {
        "run_id": "run_20240101_000000_abc123",
        "target_path": "/tmp/target",
        "workspace_path": "/tmp/workspace",
        "base_commit": "deadbeef",
        "branch": "main",
        "v1_supported": True,
    }
    defaults.update(kwargs)
    return RunMetadata(**defaults)


def _compute_eligible(meta: RunMetadata, lifecycle_state: PatchLifecycleState) -> bool:
    return (
        meta.risk_budget == "low"
        and lifecycle_state is PatchLifecycleState.VALID
        and not meta.executor_had_errors
    )


@pytest.mark.unit
def test_eligible_low_valid_no_errors():
    meta = _make_run_metadata(risk_budget="low", executor_had_errors=False)
    meta.auto_apply_eligible = _compute_eligible(meta, PatchLifecycleState.VALID)
    assert meta.auto_apply_eligible is True


@pytest.mark.unit
def test_ineligible_medium_risk():
    meta = _make_run_metadata(risk_budget="medium", executor_had_errors=False)
    meta.auto_apply_eligible = _compute_eligible(meta, PatchLifecycleState.VALID)
    assert meta.auto_apply_eligible is False


@pytest.mark.unit
def test_ineligible_high_risk():
    meta = _make_run_metadata(risk_budget="high", executor_had_errors=False)
    meta.auto_apply_eligible = _compute_eligible(meta, PatchLifecycleState.VALID)
    assert meta.auto_apply_eligible is False


@pytest.mark.unit
def test_ineligible_rebaseable():
    meta = _make_run_metadata(risk_budget="low", executor_had_errors=False)
    meta.auto_apply_eligible = _compute_eligible(meta, PatchLifecycleState.REBASEABLE)
    assert meta.auto_apply_eligible is False


@pytest.mark.unit
def test_ineligible_executor_errors():
    meta = _make_run_metadata(risk_budget="low", executor_had_errors=True)
    meta.auto_apply_eligible = _compute_eligible(meta, PatchLifecycleState.VALID)
    assert meta.auto_apply_eligible is False


@pytest.mark.unit
def test_auto_apply_eligible_field_default_false():
    meta = _make_run_metadata()
    assert meta.auto_apply_eligible is False


@pytest.mark.unit
def test_auto_apply_eligible_serialization_roundtrip():
    meta = _make_run_metadata()
    meta.auto_apply_eligible = True
    dumped = meta.model_dump_json()
    loaded = RunMetadata.model_validate_json(dumped)
    assert loaded.auto_apply_eligible is True
