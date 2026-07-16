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
import io
import os
import subprocess
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from orchestrator.schemas.audit_manifest import MANIFEST_SCHEMA_VERSION, ArtifactHash, AuditManifest
from orchestrator.workspace import WorkspaceManager

console = Console()

_TERMINAL_STATUSES = {"applied", "failed", "validation_failed"}
_CHUNK_SIZE = 65536
_MAX_MEMBER_SIZE = 500 * 1024 * 1024  # 500 MiB per tar member
_MAX_MEMBER_COUNT = 100_000


def _package_version() -> str:
    try:
        return version("orchestrator-core")
    except PackageNotFoundError:
        return "unknown"


def _read_and_hash(path: Path) -> tuple[bytes, str]:
    """Read a file once, returning its exact bytes and their SHA-256.

    Reading once and reusing the same bytes for both hashing and archiving
    guarantees the manifest hash and the archived content describe the same
    snapshot — a second, later open() of the same path could observe a
    different file if it changed between reads.
    """
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_SIZE):
            digest.update(chunk)
            chunks.append(chunk)
    return b"".join(chunks), digest.hexdigest()


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
        # Same exit code bucket as the path-collision case below: both are
        # "the output path is unusable", as opposed to code 2 (non-terminal run).
        raise typer.Exit(code=3) from exc

    bundle_path = resolved_out_dir / f"audit-{run_id}.tar.gz"
    if bundle_path.exists() and not force:
        console.print(
            f"[bold red]{bundle_path} already exists. Use --force to overwrite.[/bold red]"
        )
        raise typer.Exit(code=3)

    artifact_hashes: list[ArtifactHash] = []
    artifact_bytes: dict[str, bytes] = {}
    for file_path in run_files:
        rel_path = file_path.relative_to(run_dir).as_posix()
        data, sha256 = _read_and_hash(file_path)
        artifact_hashes.append(ArtifactHash(path=rel_path, sha256=sha256, size_bytes=len(data)))
        artifact_bytes[rel_path] = data

    manifest = AuditManifest(
        manifest_schema_version=MANIFEST_SCHEMA_VERSION,
        run_id=run_id,
        patchforge_version=_package_version(),
        bundle_created_at=datetime.now(timezone.utc),
        commit_anchor=run_metadata.base_commit,
        artifacts=artifact_hashes,
        run_metadata=run_metadata.model_dump(mode="json"),
    )
    manifest_bytes = manifest.model_dump_json(indent=2).encode("utf-8")

    top_level = f"audit-{run_id}"
    signature_bytes: Optional[bytes] = None
    if sign:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            manifest_path = tmp_dir / "manifest.json"
            manifest_path.write_bytes(manifest_bytes)
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
            signature_bytes = signature_path.read_bytes()

    # Write to a temp sibling file and publish atomically: a bundle at
    # bundle_path is either the prior good bundle (untouched) or the fully
    # written new one, never a partial file from a crash mid-write.
    tmp_bundle = bundle_path.with_name(f"{bundle_path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with tarfile.open(tmp_bundle, mode="w:gz") as tar:
            _add_tar_bytes(tar, f"{top_level}/manifest.json", manifest_bytes)
            if signature_bytes is not None:
                _add_tar_bytes(tar, f"{top_level}/manifest.json.asc", signature_bytes)
            for file_path in run_files:
                rel_path = file_path.relative_to(run_dir).as_posix()
                _add_tar_bytes(tar, f"{top_level}/artifacts/{rel_path}", artifact_bytes[rel_path])
        os.replace(tmp_bundle, bundle_path)
    except OSError as exc:
        console.print(f"[bold red]Cannot write bundle {bundle_path}: {exc}[/bold red]")
        raise typer.Exit(code=3) from exc
    finally:
        tmp_bundle.unlink(missing_ok=True)

    console.print(f"[bold green]Exported {len(run_files)} artifacts to {bundle_path}[/bold green]")
    return bundle_path


def _add_tar_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=arcname.replace("\\", "/"))
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


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
        # A corrupted/non-tar bundle is a verification failure (same bucket as
        # tampered content), not a "not found" condition — code 5, not 1.
        console.print(f"[bold red]Cannot open bundle {bundle_path}: {exc}[/bold red]")
        raise typer.Exit(code=5) from exc

    console.print(f"[bold green]Bundle {bundle_path} verified successfully[/bold green]")


def _verify_audit_open(bundle_path: Path, require_signature: bool) -> None:
    with tarfile.open(bundle_path, mode="r:gz") as tar:
        raw_members = tar.getmembers()
        if len(raw_members) > _MAX_MEMBER_COUNT:
            console.print(
                f"[bold red]Bundle exceeds the {_MAX_MEMBER_COUNT}-member limit[/bold red]"
            )
            raise typer.Exit(code=5)

        members: dict[str, tarfile.TarInfo] = {}
        seen_names: set[str] = set()
        for m in raw_members:
            if m.name in seen_names:
                console.print(f"[bold red]Duplicate member name in bundle: {m.name}[/bold red]")
                raise typer.Exit(code=5)
            seen_names.add(m.name)

            # A bundle produced by export_audit contains only plain files
            # (no directory entries, symlinks, hardlinks, or device nodes).
            if not m.isfile():
                console.print(f"[bold red]Unsupported member type in bundle: {m.name}[/bold red]")
                raise typer.Exit(code=5)

            if "\\" in m.name or Path(m.name).is_absolute() or ".." in Path(m.name).parts:
                console.print(f"[bold red]Unsafe member name in bundle: {m.name}[/bold red]")
                raise typer.Exit(code=5)

            if m.size > _MAX_MEMBER_SIZE:
                console.print(f"[bold red]Member too large: {m.name}[/bold red]")
                raise typer.Exit(code=5)

            members[m.name] = m

        # The wrapper manifest sits at "<top_level>/manifest.json" — exactly one
        # slash. This must not match "<top_level>/artifacts/manifest.json", a
        # legitimate run artifact that happens to share the filename.
        manifest_names = [n for n in members if n.endswith("/manifest.json") and n.count("/") == 1]
        if len(manifest_names) != 1:
            console.print("[bold red]Bundle does not contain exactly one manifest.json[/bold red]")
            raise typer.Exit(code=5)
        manifest_name = manifest_names[0]
        top_level = manifest_name.rsplit("/manifest.json", 1)[0]

        for name in members:
            if not name.startswith(f"{top_level}/"):
                console.print(f"[bold red]Unsafe member name in bundle: {name}[/bold red]")
                raise typer.Exit(code=5)

        manifest_bytes = _bounded_read(tar, members[manifest_name])
        try:
            manifest = AuditManifest.model_validate_json(manifest_bytes)
        except Exception as exc:
            console.print(f"[bold red]Invalid manifest.json: {exc}[/bold red]")
            raise typer.Exit(code=5) from exc

        if manifest.manifest_schema_version != MANIFEST_SCHEMA_VERSION:
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
            actual_sha256 = _bounded_hash(tar, member)
            if actual_sha256 != declared_by_path[path].sha256:
                failures.append(f"hash mismatch: {path}")

        if failures:
            console.print("[bold red]Verification failed:[/bold red]")
            for failure in failures:
                console.print(f"  - {failure}")
            raise typer.Exit(code=5)

        signature_name = f"{top_level}/manifest.json.asc"
        if signature_name in members:
            signature_bytes = _bounded_read(tar, members[signature_name])
            _verify_gpg_signature(manifest_bytes, signature_bytes)
        elif require_signature:
            console.print(
                "[bold red]--require-signature was set but no manifest.json.asc "
                "is present in the bundle[/bold red]"
            )
            raise typer.Exit(code=6)


def _bounded_read(tar: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    """Read a member fully, enforcing _MAX_MEMBER_SIZE even if the header lies."""
    extracted = tar.extractfile(member)
    if extracted is None:
        raise typer.Exit(code=5)
    chunks: list[bytes] = []
    total = 0
    while chunk := extracted.read(_CHUNK_SIZE):
        total += len(chunk)
        if total > _MAX_MEMBER_SIZE:
            raise typer.Exit(code=5)
        chunks.append(chunk)
    return b"".join(chunks)


def _bounded_hash(tar: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    """Stream-hash a member without buffering it fully, enforcing _MAX_MEMBER_SIZE."""
    extracted = tar.extractfile(member)
    if extracted is None:
        raise typer.Exit(code=5)
    digest = hashlib.sha256()
    total = 0
    while chunk := extracted.read(_CHUNK_SIZE):
        total += len(chunk)
        if total > _MAX_MEMBER_SIZE:
            raise typer.Exit(code=5)
        digest.update(chunk)
    return digest.hexdigest()


def _verify_gpg_signature(manifest_bytes: bytes, signature_bytes: bytes) -> None:
    """Verify cryptographic validity against the local GPG trust store.

    Does not enforce a signer allowlist — trust in *who* signed is delegated
    to the operator's keyring, the same model the project already uses for
    GPG-verified commits (see CONTEXT.md Invariant #6). An allowlist would be
    a new authorization feature (config surface, storage format) outside this
    issue's scope; not implemented here.
    """
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
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            console.print(f"[bold red]GPG signature verification failed: {stderr}[/bold red]")
            raise typer.Exit(code=6)
    except OSError as exc:
        console.print(f"[bold red]Cannot invoke gpg: {exc}[/bold red]")
        raise typer.Exit(code=6) from exc
    finally:
        Path(sig_path).unlink(missing_ok=True)
