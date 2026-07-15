"""Audit bundle export and verification.

``export_audit`` packages a terminal run's artifacts into a tarball with a
SHA-256 manifest (compliance-grade audit trail). ``verify_audit`` recomputes
hashes and detects tampering without ever extracting the bundle to disk.
"""

from __future__ import annotations

__all__ = [
    "export_audit",
    "verify_audit",
]

import hashlib
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from orchestrator.schemas.audit_manifest import ArtifactHash, AuditManifest
from orchestrator.workspace import WorkspaceManager

console = Console()

_TERMINAL_STATUSES = {"applied", "failed", "validation_failed"}
_CHUNK_SIZE = 65536


def _package_version() -> str:
    try:
        return version("orchestrator-core")
    except PackageNotFoundError:
        return "unknown"


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _collect_run_files(run_dir: Path) -> list[Path]:
    """Return every regular file under run_dir, sorted by POSIX-relative path.

    Symlinks are skipped (not followed, not hashed). Raises ValueError if any
    *.wal sidecar is found — its presence indicates an interrupted run.
    """
    files: list[Path] = []
    for candidate in run_dir.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if candidate.suffix == ".wal":
            raise ValueError(
                f"WAL sidecar file found: {candidate.relative_to(run_dir).as_posix()} "
                "— run was interrupted mid-write and is not audit-ready"
            )
        files.append(candidate)
    return sorted(files, key=lambda p: p.relative_to(run_dir).as_posix())


def export_audit(
    run_id: str,
    workspace: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    force: bool = False,
    sign: bool = False,
    gpg_key: Optional[str] = None,
) -> Path:
    """Export runs/<run_id>/ as a SHA-256-manifested audit tarball.

    Returns the path to the produced bundle. Raises typer.Exit on any
    documented failure mode (see docs/planning/p4/04-audit-bundle-export.md).
    """
    workspace_mgr = WorkspaceManager(workspace)

    try:
        run_dir = workspace_mgr.run_dir(run_id)
        run_metadata = workspace_mgr.read_run_json(run_id)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[bold red]Run not found: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    if run_metadata.status not in _TERMINAL_STATUSES:
        console.print(
            f"[bold red]Run {run_id} is not in a terminal state "
            f"(status={run_metadata.status!r}). Audit export requires one of "
            f"{sorted(_TERMINAL_STATUSES)}.[/bold red]"
        )
        raise typer.Exit(code=2)

    try:
        run_files = _collect_run_files(run_dir)
    except ValueError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise typer.Exit(code=2) from exc

    resolved_out_dir = Path(out_dir).resolve() if out_dir is not None else Path.cwd()
    try:
        resolved_out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        console.print(
            f"[bold red]Cannot create output directory {resolved_out_dir}: {exc}[/bold red]"
        )
        raise typer.Exit(code=2) from exc

    bundle_path = resolved_out_dir / f"audit-{run_id}.tar.gz"
    if bundle_path.exists() and not force:
        console.print(
            f"[bold red]{bundle_path} already exists. Use --force to overwrite.[/bold red]"
        )
        raise typer.Exit(code=3)

    artifact_hashes: list[ArtifactHash] = []
    for file_path in run_files:
        rel_path = file_path.relative_to(run_dir).as_posix()
        sha256, size_bytes = _sha256_file(file_path)
        artifact_hashes.append(ArtifactHash(path=rel_path, sha256=sha256, size_bytes=size_bytes))

    manifest = AuditManifest(
        run_id=run_id,
        patchforge_version=_package_version(),
        bundle_created_at=datetime.now(timezone.utc),
        commit_anchor=run_metadata.base_commit,
        artifacts=artifact_hashes,
        run_metadata=run_metadata.model_dump(mode="json"),
    )
    manifest_bytes = manifest.model_dump_json(indent=2).encode("utf-8")

    top_level = f"audit-{run_id}"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        manifest_path = tmp_dir / "manifest.json"
        manifest_path.write_bytes(manifest_bytes)

        signature_path: Optional[Path] = None
        if sign:
            signature_path = tmp_dir / "manifest.json.asc"
            cmd = ["gpg", "--batch", "--yes", "--detach-sign", "--armor"]
            if gpg_key:
                cmd += ["--local-user", gpg_key]
            cmd += ["--output", str(signature_path), str(manifest_path)]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
            except (OSError, subprocess.CalledProcessError) as exc:
                console.print(f"[bold red]GPG signing failed: {exc}[/bold red]")
                raise typer.Exit(code=4) from exc

        with tarfile.open(bundle_path, mode="w:gz") as tar:
            _add_tar_member(tar, manifest_path, f"{top_level}/manifest.json")
            if signature_path is not None:
                _add_tar_member(tar, signature_path, f"{top_level}/manifest.json.asc")
            for file_path in run_files:
                rel_path = file_path.relative_to(run_dir).as_posix()
                _add_tar_member(tar, file_path, f"{top_level}/artifacts/{rel_path}")

    console.print(f"[bold green]Exported {len(run_files)} artifacts to {bundle_path}[/bold green]")
    return bundle_path


def _add_tar_member(tar: tarfile.TarFile, source: Path, arcname: str) -> None:
    info = tar.gettarinfo(str(source), arcname=arcname)
    info.name = arcname.replace("\\", "/")
    with source.open("rb") as fh:
        tar.addfile(info, fh)


def verify_audit(bundle_path: Path, require_signature: bool = False) -> None:
    """Verify a bundle's hashes, artifact-set completeness, and optional signature.

    Reads the tarball entirely in memory — never calls tarfile.extractall().
    Raises typer.Exit on any verification failure.
    """
    if not bundle_path.exists():
        console.print(f"[bold red]Bundle not found: {bundle_path}[/bold red]")
        raise typer.Exit(code=1)

    try:
        _verify_audit_open(bundle_path, require_signature)
    except tarfile.TarError as exc:
        console.print(f"[bold red]Cannot open bundle {bundle_path}: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[bold green]Bundle {bundle_path} verified successfully[/bold green]")


def _verify_audit_open(bundle_path: Path, require_signature: bool) -> None:
    with tarfile.open(bundle_path, mode="r:gz") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}

        manifest_names = [n for n in members if n.endswith("/manifest.json")]
        if len(manifest_names) != 1:
            console.print("[bold red]Bundle does not contain exactly one manifest.json[/bold red]")
            raise typer.Exit(code=5)
        manifest_name = manifest_names[0]
        top_level = manifest_name.rsplit("/manifest.json", 1)[0]

        for name in members:
            if not name.startswith(f"{top_level}/") or ".." in Path(name).parts:
                console.print(f"[bold red]Unsafe member name in bundle: {name}[/bold red]")
                raise typer.Exit(code=5)

        manifest_bytes = _read_member(tar, members[manifest_name])
        try:
            manifest = AuditManifest.model_validate_json(manifest_bytes)
        except Exception as exc:
            console.print(f"[bold red]Invalid manifest.json: {exc}[/bold red]")
            raise typer.Exit(code=5) from exc

        if manifest.manifest_schema_version != 1:
            console.print(
                f"[bold red]Unrecognized manifest_schema_version="
                f"{manifest.manifest_schema_version}[/bold red]"
            )
            raise typer.Exit(code=7)

        artifacts_prefix = f"{top_level}/artifacts/"
        present_paths = {
            name[len(artifacts_prefix) :] for name in members if name.startswith(artifacts_prefix)
        }
        declared_paths = {a.path for a in manifest.artifacts}

        failures: list[str] = []
        for missing in sorted(declared_paths - present_paths):
            failures.append(f"missing artifact: {missing}")
        for unexpected in sorted(present_paths - declared_paths):
            failures.append(f"unexpected file: {unexpected}")

        declared_by_path = {a.path: a for a in manifest.artifacts}
        for path in sorted(declared_paths & present_paths):
            member = members[f"{artifacts_prefix}{path}"]
            data = _read_member(tar, member)
            actual_sha256 = hashlib.sha256(data).hexdigest()
            if actual_sha256 != declared_by_path[path].sha256:
                failures.append(f"hash mismatch: {path}")

        if failures:
            console.print("[bold red]Verification failed:[/bold red]")
            for failure in failures:
                console.print(f"  - {failure}")
            raise typer.Exit(code=5)

        signature_name = f"{top_level}/manifest.json.asc"
        if signature_name in members:
            signature_bytes = _read_member(tar, members[signature_name])
            _verify_gpg_signature(manifest_bytes, signature_bytes)
        elif require_signature:
            console.print(
                "[bold red]--require-signature was set but no manifest.json.asc "
                "is present in the bundle[/bold red]"
            )
            raise typer.Exit(code=6)


def _read_member(tar: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    extracted = tar.extractfile(member)
    if extracted is None:
        raise typer.Exit(code=5)
    return extracted.read()


def _verify_gpg_signature(manifest_bytes: bytes, signature_bytes: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".asc", delete=False) as sig_file:
        sig_file.write(signature_bytes)
        sig_path = sig_file.name
    try:
        result = subprocess.run(
            ["gpg", "--batch", "--verify", sig_path, "-"],
            input=manifest_bytes,
            capture_output=True,
        )
        if result.returncode != 0:
            console.print("[bold red]GPG signature verification failed[/bold red]")
            raise typer.Exit(code=6)
    except OSError as exc:
        console.print(f"[bold red]Cannot invoke gpg: {exc}[/bold red]")
        raise typer.Exit(code=6) from exc
    finally:
        Path(sig_path).unlink(missing_ok=True)
