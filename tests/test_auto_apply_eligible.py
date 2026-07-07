"""Tests for auto_apply_eligible field on RunMetadata."""

from __future__ import annotations

import pytest

from orchestrator.schemas.artifacts import (
    PatchLifecycleState,
    RunMetadata,
    compute_auto_apply_eligible,
)


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


@pytest.mark.unit
def test_eligible_low_valid_no_errors():
    assert compute_auto_apply_eligible("low", PatchLifecycleState.VALID, False) is True


@pytest.mark.unit
def test_ineligible_medium_risk():
    assert compute_auto_apply_eligible("medium", PatchLifecycleState.VALID, False) is False


@pytest.mark.unit
def test_ineligible_high_risk():
    assert compute_auto_apply_eligible("high", PatchLifecycleState.VALID, False) is False


@pytest.mark.unit
def test_ineligible_rebaseable():
    assert compute_auto_apply_eligible("low", PatchLifecycleState.REBASEABLE, False) is False


@pytest.mark.unit
def test_ineligible_executor_errors():
    assert compute_auto_apply_eligible("low", PatchLifecycleState.VALID, True) is False


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
