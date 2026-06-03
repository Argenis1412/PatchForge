import json
from datetime import datetime, timezone
from orchestrator.schemas.artifacts import generate_run_id, RunMetadata


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
        risk_budget="medium",
        max_files=5,
        max_diff_lines=500,
    )
    serialized = meta.model_dump_json()
    data = json.loads(serialized)

    assert data["run_id"] == "run_20260603_120000_abcdef"
    assert data["v1_supported"] is True
    assert data["support_reasons"] == ["Python support detected"]
    assert data["risk_budget"] == "medium"
    assert data["max_files"] == 5
    assert data["max_diff_lines"] == 500
    assert "created_at" in data

    deserialized = RunMetadata.model_validate_json(serialized)
    assert deserialized.run_id == meta.run_id
    assert deserialized.created_at == meta.created_at
