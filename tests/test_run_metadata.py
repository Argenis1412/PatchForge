from orchestrator.schemas.artifacts import RunMetadata


def _minimal_meta(**kwargs) -> RunMetadata:
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


def test_schema_version_default():
    meta = _minimal_meta()
    assert meta.schema_version == 1


def test_schema_version_serialization():
    import json

    meta = _minimal_meta()
    data = json.loads(meta.model_dump_json())
    assert data["schema_version"] == 1


def test_schema_version_backward_compatibility():
    json_str = (
        '{"run_id": "run_20260101_000000_abc123", "target_path": "/t", '
        '"workspace_path": "/w", "base_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", '
        '"branch": "main", "v1_supported": true}'
    )
    meta = RunMetadata.model_validate_json(json_str)
    assert meta.schema_version == 1


def test_schema_version_round_trip():
    meta = _minimal_meta()
    reconstructed = RunMetadata.model_validate_json(meta.model_dump_json())
    assert meta.model_dump() == reconstructed.model_dump()
