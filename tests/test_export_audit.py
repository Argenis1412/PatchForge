"""Tests for patchforge export-audit / verify-audit (issue #232)."""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from orchestrator.commands.export_audit import export_audit, verify_audit
from orchestrator.main import app
from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.workspace import WorkspaceManager

cli_runner = CliRunner()

RUN_ID = "run_20260715_120000_abcdef"


def _gpg_available() -> bool:
    return shutil.which("gpg") is not None


@pytest.fixture
def workspace_mgr(tmp_path: Path) -> WorkspaceManager:
    mgr = WorkspaceManager(tmp_path / "workspace")
    mgr.setup()
    return mgr


def _make_run(
    workspace_mgr: WorkspaceManager,
    *,
    status: str = "applied",
    run_id: str = RUN_ID,
    provider_config: dict | None = None,
    secrets_ref: str | None = None,
    env_file: str | None = None,
    staging_dir: str | None = None,
    logs_dir: str | None = None,
) -> Path:
    run_dir = workspace_mgr.create_run_directory(run_id)
    meta = RunMetadata(
        run_id=run_id,
        target_path="/dummy/target",
        workspace_path=str(workspace_mgr.root),
        base_commit="abc123def456",
        branch="main",
        v1_supported=True,
        status=status,
        provider_config=provider_config,
        secrets_ref=secrets_ref,
        env_file=env_file,
        staging_dir=staging_dir,
        logs_dir=logs_dir,
    )
    workspace_mgr.write_run_json(run_id, meta)
    (run_dir / "findings.json").write_text('{"findings": []}', encoding="utf-8")
    (run_dir / "patch.diff").write_text("diff --git a/x b/x\n", encoding="utf-8")
    return run_dir


def _exit_code(exc_info: pytest.ExceptionInfo[typer.Exit]) -> int:
    return exc_info.value.exit_code


def test_export_happy_path(workspace_mgr: WorkspaceManager, tmp_path: Path):
    _make_run(workspace_mgr, status="applied")
    out_dir = tmp_path / "out"

    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=out_dir)

    assert bundle == out_dir / f"audit-{RUN_ID}.tar.gz"
    assert bundle.exists()

    with tarfile.open(bundle, mode="r:gz") as tar:
        names = tar.getnames()
    assert f"audit-{RUN_ID}/manifest.json" in names
    assert f"audit-{RUN_ID}/artifacts/findings.json" in names
    assert f"audit-{RUN_ID}/artifacts/patch.diff" in names
    assert f"audit-{RUN_ID}/artifacts/run.json" in names


def test_export_verify_round_trip(workspace_mgr: WorkspaceManager, tmp_path: Path):
    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    verify_audit(bundle)  # must not raise


@pytest.mark.parametrize("status", ["previewed", "scanned", "planned"])
def test_export_rejects_non_terminal_status(
    workspace_mgr: WorkspaceManager, tmp_path: Path, status: str
):
    _make_run(workspace_mgr, status=status)

    with pytest.raises(typer.Exit) as exc_info:
        export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    assert _exit_code(exc_info) == 2


def test_export_validation_failed_run_is_exportable(
    workspace_mgr: WorkspaceManager, tmp_path: Path
):
    _make_run(workspace_mgr, status="validation_failed")

    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    assert bundle.exists()


def test_export_rejects_wal_sidecar(workspace_mgr: WorkspaceManager, tmp_path: Path):
    run_dir = _make_run(workspace_mgr, status="applied")
    (run_dir / "run.json.wal").write_text("{}", encoding="utf-8")

    with pytest.raises(typer.Exit) as exc_info:
        export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    assert _exit_code(exc_info) == 2


def test_export_run_not_found(workspace_mgr: WorkspaceManager, tmp_path: Path):
    with pytest.raises(typer.Exit) as exc_info:
        export_audit("run_does_not_exist", workspace=workspace_mgr.root, out_dir=tmp_path)

    assert _exit_code(exc_info) == 1


def test_export_path_collision_requires_force(workspace_mgr: WorkspaceManager, tmp_path: Path):
    _make_run(workspace_mgr, status="applied")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / f"audit-{RUN_ID}.tar.gz").write_text("stale", encoding="utf-8")

    with pytest.raises(typer.Exit) as exc_info:
        export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=out_dir)
    assert _exit_code(exc_info) == 3

    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=out_dir, force=True)
    assert bundle.exists()
    with tarfile.open(bundle, mode="r:gz") as tar:
        assert f"audit-{RUN_ID}/manifest.json" in tar.getnames()


def test_export_creates_missing_out_dir(workspace_mgr: WorkspaceManager, tmp_path: Path):
    _make_run(workspace_mgr, status="applied")
    out_dir = tmp_path / "a" / "b" / "nonexistent"

    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=out_dir)

    assert bundle.exists()


def test_export_reads_each_artifact_exactly_once(
    workspace_mgr: WorkspaceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Manifest hash and archived bytes must come from a single read (no TOCTOU)."""
    run_dir = _make_run(workspace_mgr, status="applied")
    open_counts: dict[str, int] = {}
    original_open = Path.open

    def _counting_open(self: Path, *args, **kwargs):
        open_counts[str(self)] = open_counts.get(str(self), 0) + 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _counting_open)

    export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    for artifact in ["findings.json", "patch.diff"]:
        path = str(run_dir / artifact)
        assert open_counts.get(path, 0) == 1, f"{artifact} opened {open_counts.get(path, 0)} times"


def test_export_leaves_prior_bundle_untouched_on_write_failure(
    workspace_mgr: WorkspaceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import orchestrator.commands.export_audit as module

    _make_run(workspace_mgr, status="applied")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    bundle_path = out_dir / f"audit-{RUN_ID}.tar.gz"
    bundle_path.write_bytes(b"prior good bundle bytes")

    def _raise(*args, **kwargs):
        raise OSError("simulated write failure mid-archive")

    monkeypatch.setattr(module, "_add_tar_bytes", _raise)

    with pytest.raises(typer.Exit) as exc_info:
        export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=out_dir, force=True)

    assert _exit_code(exc_info) == 3
    # The prior bundle must survive a failed write attempt untouched.
    assert bundle_path.read_bytes() == b"prior good bundle bytes"
    # No leftover .tmp-* file from the failed write.
    leftovers = list(out_dir.glob(f"audit-{RUN_ID}.tar.gz.tmp-*"))
    assert leftovers == []


def _rebuild_tarball(members: dict[str, bytes], dest: Path, top_level: str) -> None:
    with tarfile.open(dest, mode="w:gz") as tar:
        for name, data in members.items():
            import io

            info = tarfile.TarInfo(name=f"{top_level}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _read_tarball(bundle: Path) -> dict[str, bytes]:
    with tarfile.open(bundle, mode="r:gz") as tar:
        out = {}
        for member in tar.getmembers():
            if member.isfile():
                extracted = tar.extractfile(member)
                assert extracted is not None
                out[member.name] = extracted.read()
    return out


def test_verify_detects_tampered_artifact(workspace_mgr: WorkspaceManager, tmp_path: Path):
    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    top_level = f"audit-{RUN_ID}"
    members = _read_tarball(bundle)
    target_key = f"{top_level}/artifacts/patch.diff"
    members[target_key] = members[target_key] + b"TAMPERED"
    _rebuild_tarball(
        {name[len(top_level) + 1 :]: data for name, data in members.items()},
        bundle,
        top_level,
    )

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle)

    assert _exit_code(exc_info) == 5


def test_verify_detects_missing_artifact(workspace_mgr: WorkspaceManager, tmp_path: Path):
    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    top_level = f"audit-{RUN_ID}"
    members = _read_tarball(bundle)
    del members[f"{top_level}/artifacts/patch.diff"]
    _rebuild_tarball(
        {name[len(top_level) + 1 :]: data for name, data in members.items()},
        bundle,
        top_level,
    )

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle)

    assert _exit_code(exc_info) == 5


def test_verify_detects_path_traversal_member(workspace_mgr: WorkspaceManager, tmp_path: Path):
    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    members = _read_tarball(bundle)
    members["../../evil"] = b"payload"
    with tarfile.open(bundle, mode="w:gz") as tar:
        import io

        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle)

    assert _exit_code(exc_info) == 5


def test_verify_detects_artifact_injection(workspace_mgr: WorkspaceManager, tmp_path: Path):
    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    top_level = f"audit-{RUN_ID}"
    members = _read_tarball(bundle)
    members[f"{top_level}/artifacts/malicious.sh"] = b"#!/bin/sh\nrm -rf /\n"
    _rebuild_tarball(
        {name[len(top_level) + 1 :]: data for name, data in members.items()},
        bundle,
        top_level,
    )

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle)

    assert _exit_code(exc_info) == 5


def test_verify_rejects_duplicate_member_names(workspace_mgr: WorkspaceManager, tmp_path: Path):
    import io

    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    members = _read_tarball(bundle)
    with tarfile.open(bundle, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        # Duplicate the manifest entry under the same name.
        manifest_name = f"audit-{RUN_ID}/manifest.json"
        dup_data = members[manifest_name]
        info = tarfile.TarInfo(name=manifest_name)
        info.size = len(dup_data)
        tar.addfile(info, io.BytesIO(dup_data))

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle)

    assert _exit_code(exc_info) == 5


def test_verify_rejects_non_regular_member_type(workspace_mgr: WorkspaceManager, tmp_path: Path):
    import io

    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    members = _read_tarball(bundle)
    with tarfile.open(bundle, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        symlink_info = tarfile.TarInfo(name=f"audit-{RUN_ID}/artifacts/sneaky-link")
        symlink_info.type = tarfile.SYMTYPE
        symlink_info.linkname = "/etc/passwd"
        tar.addfile(symlink_info)

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle)

    assert _exit_code(exc_info) == 5


def test_verify_rejects_backslash_in_member_name(workspace_mgr: WorkspaceManager, tmp_path: Path):
    import io

    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    members = _read_tarball(bundle)
    with tarfile.open(bundle, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo(name=f"audit-{RUN_ID}/artifacts/evil\\payload")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"evil"))

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle)

    assert _exit_code(exc_info) == 5


def test_verify_rejects_oversized_member(
    workspace_mgr: WorkspaceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import orchestrator.commands.export_audit as module

    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    monkeypatch.setattr(module, "_MAX_MEMBER_SIZE", 4)

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle)

    assert _exit_code(exc_info) == 5


def test_export_preserves_legitimate_manifest_json_artifact(
    workspace_mgr: WorkspaceManager, tmp_path: Path
):
    run_dir = _make_run(workspace_mgr, status="applied")
    (run_dir / "manifest.json").write_text('{"model": "deployed-v1"}', encoding="utf-8")

    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    top_level = f"audit-{RUN_ID}"
    with tarfile.open(bundle, mode="r:gz") as tar:
        names = tar.getnames()
        assert f"{top_level}/artifacts/manifest.json" in names
        assert f"{top_level}/manifest.json" in names
        member = tar.getmember(f"{top_level}/artifacts/manifest.json")
        extracted = tar.extractfile(member)
        assert extracted is not None
        assert extracted.read() == b'{"model": "deployed-v1"}'

    verify_audit(bundle)  # must not raise


def test_manifest_mirrors_run_metadata_completeness(
    workspace_mgr: WorkspaceManager, tmp_path: Path
):
    import json

    _make_run(
        workspace_mgr,
        status="applied",
        provider_config={"architect": "claude-opus-4-7"},
    )
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    with tarfile.open(bundle, mode="r:gz") as tar:
        member = tar.getmember(f"audit-{RUN_ID}/manifest.json")
        extracted = tar.extractfile(member)
        assert extracted is not None
        manifest = json.loads(extracted.read())

    run_metadata = manifest["run_metadata"]
    expected_fields = set(RunMetadata.model_fields.keys())
    assert expected_fields.issubset(run_metadata.keys())
    assert run_metadata["provider_config"] == {"architect": "claude-opus-4-7"}


def test_manifest_provider_config_none_when_pre_executor(
    workspace_mgr: WorkspaceManager, tmp_path: Path
):
    import json

    _make_run(workspace_mgr, status="validation_failed", provider_config=None)
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    with tarfile.open(bundle, mode="r:gz") as tar:
        member = tar.getmember(f"audit-{RUN_ID}/manifest.json")
        extracted = tar.extractfile(member)
        assert extracted is not None
        manifest = json.loads(extracted.read())

    assert manifest["run_metadata"]["provider_config"] is None


# workspace_path/target_path are not listed here: _make_run always sets them
# (non-None), so they are covered directly in the assertions below.
_REDACT_FIELD_VALUES = {
    "secrets_ref": "vault://secrets/prod",
    "env_file": "/dummy/target/.env",
    "staging_dir": "/dummy/staging",
    "logs_dir": "/dummy/logs",
    "provider_config": {"architect": "claude-opus-4-7"},
}


def test_export_redact_replaces_sensitive_fields(workspace_mgr: WorkspaceManager, tmp_path: Path):
    import json

    _make_run(
        workspace_mgr,
        status="applied",
        provider_config=_REDACT_FIELD_VALUES["provider_config"],
        secrets_ref=_REDACT_FIELD_VALUES["secrets_ref"],
        env_file=_REDACT_FIELD_VALUES["env_file"],
        staging_dir=_REDACT_FIELD_VALUES["staging_dir"],
        logs_dir=_REDACT_FIELD_VALUES["logs_dir"],
    )
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path, redact=True)

    members = _read_tarball(bundle)
    top_level = f"audit-{RUN_ID}"
    manifest = json.loads(members[f"{top_level}/manifest.json"])
    run_json = json.loads(members[f"{top_level}/artifacts/run.json"])

    from orchestrator.commands.export_audit import _PUBLIC_FIELDS, _REDACT_FIELDS

    for doc in (manifest["run_metadata"], run_json):
        for field in _REDACT_FIELDS:
            assert doc[field] == "[REDACTED]", f"{field} was not redacted: {doc[field]!r}"
        # workspace_path/target_path are always set by _make_run (non-None), so
        # they must be redacted too.
        assert doc["workspace_path"] == "[REDACTED]"
        assert doc["target_path"] == "[REDACTED]"
        # Public fields must survive untouched.
        assert doc["run_id"] == RUN_ID
        assert doc["branch"] == "main"
        assert doc["base_commit"] == "abc123def456"
        for field in _PUBLIC_FIELDS:
            assert doc[field] != "[REDACTED]"


def test_export_redact_preserves_none_values(workspace_mgr: WorkspaceManager, tmp_path: Path):
    """Fields never set (None) must stay None, not become '[REDACTED]'."""
    import json

    _make_run(workspace_mgr, status="applied")  # secrets_ref/env_file/etc. default to None
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path, redact=True)

    members = _read_tarball(bundle)
    top_level = f"audit-{RUN_ID}"
    manifest = json.loads(members[f"{top_level}/manifest.json"])
    run_json = json.loads(members[f"{top_level}/artifacts/run.json"])

    for doc in (manifest["run_metadata"], run_json):
        assert doc["secrets_ref"] is None
        assert doc["env_file"] is None
        assert doc["staging_dir"] is None
        assert doc["logs_dir"] is None
        assert doc["provider_config"] is None
        # workspace_path/target_path are always set, so they're still redacted.
        assert doc["workspace_path"] == "[REDACTED]"
        assert doc["target_path"] == "[REDACTED]"


def test_export_default_preserves_all_fields(workspace_mgr: WorkspaceManager, tmp_path: Path):
    """Without --redact, both manifest and run.json keep the full mirror."""
    import json

    _make_run(
        workspace_mgr,
        status="applied",
        provider_config=_REDACT_FIELD_VALUES["provider_config"],
        secrets_ref=_REDACT_FIELD_VALUES["secrets_ref"],
        env_file=_REDACT_FIELD_VALUES["env_file"],
        staging_dir=_REDACT_FIELD_VALUES["staging_dir"],
        logs_dir=_REDACT_FIELD_VALUES["logs_dir"],
    )
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    members = _read_tarball(bundle)
    top_level = f"audit-{RUN_ID}"
    manifest = json.loads(members[f"{top_level}/manifest.json"])
    run_json = json.loads(members[f"{top_level}/artifacts/run.json"])

    for doc in (manifest["run_metadata"], run_json):
        assert doc["secrets_ref"] == _REDACT_FIELD_VALUES["secrets_ref"]
        assert doc["env_file"] == _REDACT_FIELD_VALUES["env_file"]
        assert doc["staging_dir"] == _REDACT_FIELD_VALUES["staging_dir"]
        assert doc["logs_dir"] == _REDACT_FIELD_VALUES["logs_dir"]
        assert doc["provider_config"] == _REDACT_FIELD_VALUES["provider_config"]


def test_redacted_bundle_verifies(workspace_mgr: WorkspaceManager, tmp_path: Path):
    """verify-audit must pass on a --redact bundle: run.json hash matches its redacted bytes."""
    _make_run(
        workspace_mgr,
        status="applied",
        secrets_ref=_REDACT_FIELD_VALUES["secrets_ref"],
    )
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path, redact=True)

    verify_audit(bundle)  # must not raise


def test_redact_with_sign(
    workspace_mgr: WorkspaceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """--redact --sign must sign the redacted manifest, and verify-audit must pass."""
    import orchestrator.commands.export_audit as module

    _make_run(
        workspace_mgr,
        status="applied",
        secrets_ref=_REDACT_FIELD_VALUES["secrets_ref"],
    )

    def _fake_run(cmd, **kwargs):
        if "--verify" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
        output_path = Path(cmd[cmd.index("--output") + 1])
        output_path.write_text("-----BEGIN PGP SIGNATURE-----\nfake\n-----END PGP SIGNATURE-----\n")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", _fake_run)

    bundle = export_audit(
        RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path, redact=True, sign=True
    )

    with tarfile.open(bundle, mode="r:gz") as tar:
        assert f"audit-{RUN_ID}/manifest.json.asc" in tar.getnames()

    verify_audit(bundle)  # must not raise


def test_redact_fields_cover_all_run_metadata_fields():
    """Anti-rot guard: every RunMetadata field must be classified as redact-worthy
    or public. A new field added without classification fails this test."""
    from orchestrator.commands.export_audit import _PUBLIC_FIELDS, _REDACT_FIELDS

    all_fields = set(RunMetadata.model_fields.keys())
    classified = _REDACT_FIELDS | _PUBLIC_FIELDS

    assert not (_REDACT_FIELDS & _PUBLIC_FIELDS), "a field cannot be both redacted and public"
    assert classified == all_fields, (
        f"unclassified RunMetadata fields: {all_fields - classified}; "
        f"stale entries no longer on RunMetadata: {classified - all_fields}"
    )


def test_export_deterministic_artifact_ordering(workspace_mgr: WorkspaceManager, tmp_path: Path):
    import json

    _make_run(workspace_mgr, status="applied")
    bundle1 = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path / "one")
    bundle2 = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path / "two")

    def _paths(bundle: Path) -> list[str]:
        with tarfile.open(bundle, mode="r:gz") as tar:
            member = tar.getmember(f"audit-{RUN_ID}/manifest.json")
            extracted = tar.extractfile(member)
            assert extracted is not None
            manifest = json.loads(extracted.read())
        return [a["path"] for a in manifest["artifacts"]]

    paths1 = _paths(bundle1)
    paths2 = _paths(bundle2)
    assert paths1 == paths2
    assert paths1 == sorted(paths1)
    assert all("/" in p or p for p in paths1)
    assert not any("\\" in p for p in paths1)


def test_verify_rejects_unknown_manifest_schema_version(
    workspace_mgr: WorkspaceManager, tmp_path: Path
):
    import io
    import json

    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    top_level = f"audit-{RUN_ID}"
    members = _read_tarball(bundle)
    manifest = json.loads(members[f"{top_level}/manifest.json"])
    manifest["manifest_schema_version"] = 99
    members[f"{top_level}/manifest.json"] = json.dumps(manifest).encode("utf-8")

    with tarfile.open(bundle, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle)

    assert _exit_code(exc_info) == 7


def test_manifest_extra_field_rejected():
    from pydantic import ValidationError

    from orchestrator.schemas.audit_manifest import AuditManifest

    with pytest.raises(ValidationError):
        AuditManifest.model_validate(
            {
                "manifest_schema_version": 1,
                "run_id": "x",
                "patchforge_version": "1.1.0",
                "bundle_created_at": "2026-07-15T00:00:00Z",
                "commit_anchor": "abc",
                "artifacts": [],
                "run_metadata": {},
                "unexpected_field": "should not be allowed",
            }
        )


def test_manifest_schema_version_required():
    from pydantic import ValidationError

    from orchestrator.schemas.audit_manifest import AuditManifest

    with pytest.raises(ValidationError):
        AuditManifest.model_validate(
            {
                "run_id": "x",
                "patchforge_version": "1.1.0",
                "bundle_created_at": "2026-07-15T00:00:00Z",
                "commit_anchor": "abc",
                "artifacts": [],
                "run_metadata": {},
            }
        )


@pytest.mark.skipif(not _gpg_available(), reason="gpg not available in PATH")
def test_gpg_sign_and_verify(workspace_mgr: WorkspaceManager, tmp_path: Path):
    _make_run(workspace_mgr, status="applied")
    try:
        bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path, sign=True)
    except typer.Exit as exc:
        if exc.exit_code != 4:
            raise
        pytest.skip(f"gpg present but signing unavailable (exit {exc.exit_code})")

    with tarfile.open(bundle, mode="r:gz") as tar:
        assert f"audit-{RUN_ID}/manifest.json.asc" in tar.getnames()

    verify_audit(bundle)
    verify_audit(bundle, require_signature=True)


@pytest.mark.skipif(not _gpg_available(), reason="gpg not available in PATH")
def test_gpg_signature_stripping_requires_signature_flag(
    workspace_mgr: WorkspaceManager, tmp_path: Path
):
    import io

    _make_run(workspace_mgr, status="applied")
    try:
        bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path, sign=True)
    except typer.Exit as exc:
        if exc.exit_code != 4:
            raise
        pytest.skip(f"gpg present but signing unavailable (exit {exc.exit_code})")

    top_level = f"audit-{RUN_ID}"
    members = _read_tarball(bundle)
    del members[f"{top_level}/manifest.json.asc"]
    with tarfile.open(bundle, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    verify_audit(bundle)  # no --require-signature: absence is accepted

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle, require_signature=True)
    assert _exit_code(exc_info) == 6


@pytest.mark.skipif(not _gpg_available(), reason="gpg not available in PATH")
def test_gpg_mutated_signature_fails_verify(workspace_mgr: WorkspaceManager, tmp_path: Path):
    import io

    _make_run(workspace_mgr, status="applied")
    try:
        bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path, sign=True)
    except typer.Exit as exc:
        if exc.exit_code != 4:
            raise
        pytest.skip(f"gpg present but signing unavailable (exit {exc.exit_code})")

    top_level = f"audit-{RUN_ID}"
    members = _read_tarball(bundle)
    sig_key = f"{top_level}/manifest.json.asc"
    members[sig_key] = members[sig_key] + b"\nMUTATED\n"
    with tarfile.open(bundle, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle)
    assert _exit_code(exc_info) == 6


def test_package_version_fallback(monkeypatch: pytest.MonkeyPatch):
    from importlib.metadata import PackageNotFoundError

    import orchestrator.commands.export_audit as module

    def _raise(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(module, "version", _raise)
    assert module._package_version() == "unknown"


def test_export_defaults_out_dir_to_cwd(
    workspace_mgr: WorkspaceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _make_run(workspace_mgr, status="applied")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root)

    assert bundle == cwd / f"audit-{RUN_ID}.tar.gz"
    assert bundle.exists()


def test_export_out_dir_mkdir_failure(
    workspace_mgr: WorkspaceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _make_run(workspace_mgr, status="applied")

    def _raise_mkdir(self, *args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "mkdir", _raise_mkdir)

    with pytest.raises(typer.Exit) as exc_info:
        export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path / "out")

    assert _exit_code(exc_info) == 3


def test_export_gpg_sign_failure_exits_4(
    workspace_mgr: WorkspaceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import orchestrator.commands.export_audit as module

    _make_run(workspace_mgr, status="applied")

    def _raise(cmd, **kwargs):
        raise subprocess.CalledProcessError(2, cmd, stderr=b"gpg: signing failed: No secret key")

    monkeypatch.setattr(module.subprocess, "run", _raise)

    with pytest.raises(typer.Exit) as exc_info:
        export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path, sign=True)

    assert _exit_code(exc_info) == 4


def test_export_gpg_missing_binary_exits_4(
    workspace_mgr: WorkspaceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import orchestrator.commands.export_audit as module

    _make_run(workspace_mgr, status="applied")

    def _raise(cmd, **kwargs):
        raise OSError("gpg not found")

    monkeypatch.setattr(module.subprocess, "run", _raise)

    with pytest.raises(typer.Exit) as exc_info:
        export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path, sign=True)

    assert _exit_code(exc_info) == 4


def test_export_sign_writes_signature_file_without_real_gpg(
    workspace_mgr: WorkspaceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Mocks gpg so signature-inclusion behavior is verified even in CI without gpg."""
    import orchestrator.commands.export_audit as module

    _make_run(workspace_mgr, status="applied")
    captured_cmd: list[str] = []

    def _fake_run(cmd, **kwargs):
        captured_cmd[:] = cmd
        output_path = Path(cmd[cmd.index("--output") + 1])
        output_path.write_text("-----BEGIN PGP SIGNATURE-----\nfake\n-----END PGP SIGNATURE-----\n")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", _fake_run)

    bundle = export_audit(
        RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path, sign=True, gpg_key="ABCD1234"
    )

    with tarfile.open(bundle, mode="r:gz") as tar:
        assert f"audit-{RUN_ID}/manifest.json.asc" in tar.getnames()

    assert "--local-user" in captured_cmd
    assert "ABCD1234" in captured_cmd


def test_verify_bundle_not_found(tmp_path: Path):
    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(tmp_path / "does-not-exist.tar.gz")

    assert _exit_code(exc_info) == 1


def test_verify_corrupted_archive_exits_5(tmp_path: Path):
    bad_bundle = tmp_path / "corrupt.tar.gz"
    bad_bundle.write_bytes(b"not a real gzip tarball")

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bad_bundle)

    assert _exit_code(exc_info) == 5


def test_verify_invalid_manifest_json_exits_5(workspace_mgr: WorkspaceManager, tmp_path: Path):
    import io

    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    top_level = f"audit-{RUN_ID}"
    members = _read_tarball(bundle)
    members[f"{top_level}/manifest.json"] = b"{not valid json"
    with tarfile.open(bundle, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    with pytest.raises(typer.Exit) as exc_info:
        verify_audit(bundle)

    assert _exit_code(exc_info) == 5


def test_cli_export_audit_gpg_key_passthrough(
    workspace_mgr: WorkspaceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import orchestrator.commands.export_audit as module

    _make_run(workspace_mgr, status="applied")
    captured_cmd: list[str] = []

    def _fake_run(cmd, **kwargs):
        captured_cmd[:] = cmd
        output_path = Path(cmd[cmd.index("--output") + 1])
        output_path.write_text("fake-signature")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", _fake_run)

    result = cli_runner.invoke(
        app,
        [
            "export-audit",
            RUN_ID,
            "--workspace",
            str(workspace_mgr.root),
            "--out",
            str(tmp_path),
            "--sign",
            "--gpg-key",
            "DEADBEEF",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "--local-user" in captured_cmd
    assert "DEADBEEF" in captured_cmd


def test_cli_verify_audit_require_signature_passthrough(
    workspace_mgr: WorkspaceManager, tmp_path: Path
):
    _make_run(workspace_mgr, status="applied")
    bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path)

    result = cli_runner.invoke(app, ["verify-audit", str(bundle), "--require-signature"])

    assert result.exit_code == 6, result.output


# ---------------------------------------------------------------------------
# Repo lock integration tests (issue #235)
# ---------------------------------------------------------------------------

_REPO_IDENTITY = str(Path("/dummy/target").resolve())


def test_export_audit_acquires_and_releases_lock(workspace_mgr: WorkspaceManager, tmp_path: Path):
    from orchestrator.storage.lock import acquire_repo_lock

    _make_run(workspace_mgr, status="applied")
    db_dir = tmp_path / "coord"
    db_dir.mkdir()

    export_audit(
        RUN_ID,
        workspace=workspace_mgr.root,
        out_dir=tmp_path / "out",
        worker_id="w1",
        coordination_db_dir=db_dir,
    )

    assert acquire_repo_lock(_REPO_IDENTITY, "w2", db_dir=db_dir)


def test_export_audit_fails_when_repo_locked(workspace_mgr: WorkspaceManager, tmp_path: Path):
    from orchestrator.storage.lock import acquire_repo_lock

    _make_run(workspace_mgr, status="applied")
    db_dir = tmp_path / "coord"
    db_dir.mkdir()

    assert acquire_repo_lock(_REPO_IDENTITY, "other-worker", db_dir=db_dir)

    with pytest.raises(typer.Exit) as exc_info:
        export_audit(
            RUN_ID,
            workspace=workspace_mgr.root,
            out_dir=tmp_path / "out",
            worker_id="w1",
            coordination_db_dir=db_dir,
        )

    assert _exit_code(exc_info) == 8


def test_export_audit_releases_lock_on_non_terminal_status_failure(
    workspace_mgr: WorkspaceManager, tmp_path: Path
):
    from orchestrator.storage.lock import acquire_repo_lock

    _make_run(workspace_mgr, status="planned")
    db_dir = tmp_path / "coord"
    db_dir.mkdir()

    with pytest.raises(typer.Exit) as exc_info:
        export_audit(
            RUN_ID,
            workspace=workspace_mgr.root,
            out_dir=tmp_path / "out",
            worker_id="w1",
            coordination_db_dir=db_dir,
        )

    assert _exit_code(exc_info) == 2
    assert acquire_repo_lock(_REPO_IDENTITY, "w2", db_dir=db_dir)


def test_export_audit_concurrent_no_worker_id_blocked(
    workspace_mgr: WorkspaceManager, tmp_path: Path
):
    from orchestrator.storage.lock import acquire_repo_lock

    _make_run(workspace_mgr, status="applied")
    db_dir = tmp_path / "coord"
    db_dir.mkdir()

    assert acquire_repo_lock(_REPO_IDENTITY, "first-caller", db_dir=db_dir)

    with pytest.raises(typer.Exit) as exc_info:
        export_audit(
            RUN_ID,
            workspace=workspace_mgr.root,
            out_dir=tmp_path / "out",
            coordination_db_dir=db_dir,
        )

    assert _exit_code(exc_info) == 8
