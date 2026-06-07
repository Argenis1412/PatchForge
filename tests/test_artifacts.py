import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from orchestrator.schemas.artifacts import PatchLifecycleState, RunMetadata, generate_run_id


def test_generate_run_id_format():
    run_id = generate_run_id()
    assert run_id.startswith("run_")
    # format: run_YYYYMMDD_HHMMSS_xxxxxx
    parts = run_id.split("_")
    assert len(parts) == 4
    assert parts[0] == "run"
    assert len(parts[1]) == 8  # YYYYMMDD
    assert len(parts[2]) == 6  # HHMMSS
    assert len(parts[3]) == 6  # short hash


def test_generate_run_id_uniqueness():
    with patch("orchestrator.schemas.artifacts.uuid.uuid4") as mock_uuid:
        mock_uuid.side_effect = [uuid.UUID(hex=f"{i:06x}".ljust(32, "0")) for i in range(100)]
        ids = {generate_run_id() for _ in range(100)}
    assert len(ids) == 100  # No collisions in 100 rapid generations


def test_run_metadata_serialization():
    meta = RunMetadata(
        run_id="run_20260603_120000_abcdef",
        target_path="/dummy/target",
        workspace_path="/dummy/workspace",
        base_commit="1234567890abcdef1234567890abcdef12345678",
        branch="main",
        status="scanning",
        created_at=datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc),
        v1_supported=True,
        support_reasons=["Python support detected"],
        risk_budget="low",
        max_files=2,
        max_diff_lines=100,
    )
    serialized = meta.model_dump_json()
    data = json.loads(serialized)

    assert data["run_id"] == "run_20260603_120000_abcdef"
    assert data["v1_supported"] is True
    assert data["support_reasons"] == ["Python support detected"]
    assert data["risk_budget"] == "low"
    assert data["max_files"] == 2
    assert data["max_diff_lines"] == 100
    assert "created_at" in data

    deserialized = RunMetadata.model_validate_json(serialized)
    assert deserialized.run_id == meta.run_id
    assert deserialized.created_at == meta.created_at


def test_run_metadata_backward_compatibility():
    """Values from old format must still validate after schema hardening."""
    meta = RunMetadata(
        run_id="run_20260101_000000_old000",
        target_path="/legacy/target",
        workspace_path="/legacy/workspace",
        base_commit="a" * 40,
        branch="main",
        v1_supported=True,
        risk_budget="medium",
        max_files=5,
        max_diff_lines=500,
    )
    serialized = meta.model_dump_json()
    deserialized = RunMetadata.model_validate_json(serialized)
    assert deserialized.risk_budget == "medium"
    assert deserialized.max_files == 5
    assert deserialized.max_diff_lines == 500


def _base_meta(**kwargs) -> RunMetadata:
    """Return a minimal valid RunMetadata, allowing field overrides."""
    defaults = dict(
        run_id="run_20260101_000000_abc123",
        target_path="/t",
        workspace_path="/w",
        base_commit="a" * 40,
        branch="main",
        v1_supported=True,
    )
    defaults.update(kwargs)
    return RunMetadata(**defaults)


@pytest.mark.parametrize("state", list(PatchLifecycleState))
def test_run_metadata_lifecycle_state_all_valid_values(state):
    """Every PatchLifecycleState member must be accepted by RunMetadata."""
    meta = _base_meta(lifecycle_state=state)
    assert meta.lifecycle_state == state

    # Serialise → deserialise round-trip preserves the value.
    rt = RunMetadata.model_validate_json(meta.model_dump_json())
    assert rt.lifecycle_state == state


def test_run_metadata_lifecycle_state_accepts_string_coercion():
    """Plain string values that match enum members must be coerced by Pydantic."""
    meta = _base_meta(lifecycle_state="VALID")
    assert meta.lifecycle_state is PatchLifecycleState.VALID


def test_run_metadata_lifecycle_state_rejects_invalid_value():
    """An unrecognised lifecycle_state string must raise ValidationError."""
    with pytest.raises(ValidationError):
        _base_meta(lifecycle_state="UNKNOWN_STATE")
