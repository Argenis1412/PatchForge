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
                "run_id": "x",
                "patchforge_version": "1.1.0",
                "bundle_created_at": "2026-07-15T00:00:00Z",
                "commit_anchor": "abc",
                "artifacts": [],
                "run_metadata": {},
                "unexpected_field": "should not be allowed",
            }
        )


@pytest.mark.skipif(not _gpg_available(), reason="gpg not available in PATH")
def test_gpg_sign_and_verify(workspace_mgr: WorkspaceManager, tmp_path: Path):
    _make_run(workspace_mgr, status="applied")
    try:
        bundle = export_audit(RUN_ID, workspace=workspace_mgr.root, out_dir=tmp_path, sign=True)
    except typer.Exit as exc:
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
