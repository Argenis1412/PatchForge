import pytest

from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.workspace import WorkspaceManager


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


# B2 tests — execution context fields


def test_run_metadata_serialization():
    """All B2 fields serialize and deserialize correctly."""
    meta = _minimal_meta(
        issue_number=42,
        logs_dir="/workspace/logs",
        staging_dir="/workspace/staging",
        trace_id="trace-abc-123",
        env_file="/etc/patchforge/.env",
        worker_id="worker-01",
        secrets_ref="vault/secret/patchforge",
        provider_config={"provider": "anthropic", "model": "claude-sonnet-4-6"},
        current_stage="plan",
    )
    reconstructed = RunMetadata.model_validate_json(meta.model_dump_json())
    assert reconstructed.issue_number == 42
    assert reconstructed.logs_dir == "/workspace/logs"
    assert reconstructed.staging_dir == "/workspace/staging"
    assert reconstructed.trace_id == "trace-abc-123"
    assert reconstructed.env_file == "/etc/patchforge/.env"
    assert reconstructed.worker_id == "worker-01"
    assert reconstructed.secrets_ref == "vault/secret/patchforge"
    assert reconstructed.provider_config == {"provider": "anthropic", "model": "claude-sonnet-4-6"}
    assert reconstructed.current_stage == "plan"


def test_run_metadata_issue_number():
    """issue_number round-trips through RunMetadata serialization."""
    meta = _minimal_meta(issue_number=99)
    reconstructed = RunMetadata.model_validate_json(meta.model_dump_json())
    assert reconstructed.issue_number == 99


def test_run_metadata_b2_fields_default_none():
    """B2 fields are None by default — existing serialization is preserved."""
    meta = _minimal_meta()
    assert meta.issue_number is None
    assert meta.logs_dir is None
    assert meta.staging_dir is None
    assert meta.trace_id is None
    assert meta.env_file is None
    assert meta.worker_id is None
    assert meta.secrets_ref is None
    assert meta.provider_config is None
    assert meta.current_stage is None


def test_workspace_manager_env_var_fallback(tmp_path, monkeypatch):
    """WorkspaceManager() without args falls back to PATCHFORGE_WORKSPACE env var."""
    monkeypatch.setenv("PATCHFORGE_WORKSPACE", str(tmp_path))
    mgr = WorkspaceManager()
    assert mgr.root == tmp_path.resolve()


def test_workspace_manager_explicit_path_overrides_env(tmp_path, monkeypatch):
    """WorkspaceManager(workspace_path=Path(...)) overrides PATCHFORGE_WORKSPACE."""
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("PATCHFORGE_WORKSPACE", str(tmp_path))
    mgr = WorkspaceManager(workspace_path=other)
    assert mgr.root == other.resolve()


def test_workspace_manager_no_args_no_env_raises(monkeypatch):
    """WorkspaceManager() with neither arg nor env var raises ValueError."""
    monkeypatch.delenv("PATCHFORGE_WORKSPACE", raising=False)
    with pytest.raises(ValueError, match="PATCHFORGE_WORKSPACE"):
        WorkspaceManager()
