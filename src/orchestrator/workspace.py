from __future__ import annotations

import json
import os
import re
from pathlib import Path

from orchestrator.safety import validate_filename
from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.schemas.experiment import Experiment, Verdict
from orchestrator.storage import _wal_write
from orchestrator.storage.artifact_store import ArtifactStore
from orchestrator.storage.local_store import LocalArtifactStore

# Only allow alphanumeric characters, underscores, and hyphens in run IDs and worker IDs.
_RUN_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_WORKER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_run_id(run_id: str) -> None:
    """Raise ValueError if run_id is empty or contains path-traversal characters."""
    if not run_id or not _RUN_ID_RE.match(run_id):
        raise ValueError(
            f"Invalid run_id {run_id!r}. "
            "Only alphanumeric characters, underscores, and hyphens are allowed."
        )


class WorkspaceManager:
    def __init__(
        self,
        workspace_path: Path | None = None,
        worker_id: str = "",
        *,
        store: ArtifactStore | None = None,
    ):
        if workspace_path is not None:
            resolved = Path(workspace_path).resolve()
        else:
            env_val = os.environ.get("PATCHFORGE_WORKSPACE")
            if not env_val:
                raise ValueError(
                    "WorkspaceManager requires either a workspace_path argument "
                    "or the PATCHFORGE_WORKSPACE environment variable to be set."
                )
            resolved = Path(env_val).resolve()
        self.root = resolved
        if worker_id:
            if not _WORKER_ID_RE.match(worker_id):
                raise ValueError(
                    f"Invalid worker_id {worker_id!r}. "
                    "Only alphanumeric characters, underscores, and hyphens are allowed."
                )
            self.root = self.root / worker_id
        self.runs = self.root / "runs"
        self.store = store or LocalArtifactStore(self.runs)
        self.logs = self.root / "logs"
        self.prompts = self.root / "prompts"
        self.outputs = self.root / "outputs"
        self.cache = self.root / "cache"
        self.temp = self.root / "temp"
        self.manifest = self.outputs / "manifest.json"
        self._worker_id = worker_id

    def touch_heartbeat(self) -> None:
        (self.root / ".workspace").touch()

    def setup(self) -> None:
        """Create all workspace directories if they do not exist."""
        for directory in [
            self.root,
            self.runs,
            self.logs,
            self.prompts,
            self.outputs,
            self.cache,
            self.temp,
        ]:
            directory.mkdir(parents=True, exist_ok=True)
        self.touch_heartbeat()

    def staging_dir_for_run(self, run_id: str) -> Path:
        path = self.outputs / "staging" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup_stale_workspaces(self, max_age_hours: int = 24) -> None:
        import shutil
        import time

        if not self._worker_id:
            return
        base_dir = self.root.parent
        now = time.time()
        cutoff = now - (max_age_hours * 3600)
        for child in base_dir.iterdir():
            if child.is_dir() and child.name.startswith("worker-"):
                hb = child / ".workspace"
                mtime = hb.stat().st_mtime if hb.exists() else child.stat().st_mtime
                if mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)

    def read_manifest(self) -> dict:
        if not self.manifest.exists():
            return {"version": 1, "latest": {}}
        try:
            return json.loads(self.manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"version": 1, "latest": {}}

    def update_manifest(self, stage: str, filename: str) -> None:
        manifest = self.read_manifest()
        manifest.setdefault("latest", {})[stage] = filename
        self.outputs.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # --- V1 Run-centric methods ---

    def run_dir(self, run_id: str) -> Path:
        """Get the runs/{run_id} directory path (does not create it)."""
        _validate_run_id(run_id)
        return self.runs / run_id

    def create_run_directory(self, run_id: str) -> Path:
        """Create and return the runs/{run_id} directory. Only scan should call this."""
        _validate_run_id(run_id)
        path = self.runs / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_artifact(self, run_id: str, name: str, data: str) -> str:
        """Write content to an artifact file, delegating to the configured store.

        Raises FileNotFoundError if the run does not exist yet.
        Returns the store ref (absolute path for LocalArtifactStore).
        """
        self.ensure_run_exists(run_id)
        safe_name = validate_filename(name)
        ref = self.store.write(f"{run_id}/{safe_name}", data).ref
        return ref

    def _write_artifact_unchecked(self, run_id: str, name: str, data: str) -> str:
        """Write to an artifact path without requiring the run to pre-exist.

        Used exclusively by write_run_json during the initial creation sequence
        inside scan (after create_run_directory but before the first run.json exists).
        """
        safe_name = validate_filename(name)
        ref = self.store.write(f"{run_id}/{safe_name}", data).ref
        return ref

    def read_artifact(self, run_id: str, name: str) -> str:
        """Read content from an artifact, falling back to local copy on store failure."""
        safe_name = validate_filename(name)
        try:
            return self.store.read(f"{run_id}/{safe_name}")
        except Exception:
            local = self.run_dir(run_id) / safe_name
            if local.exists():
                return local.read_text(encoding="utf-8")
            raise

    def write_run_json(self, run_id: str, metadata: RunMetadata) -> Path:
        """Write the run.json metadata file.

        Uses the unchecked writer so it can be called both during initial
        creation (scan) and during subsequent status updates.
        Dual-writes to the configured store (best-effort).
        """
        import traceback

        run_dir = self.run_dir(run_id)
        path = run_dir / "run.json"
        data = metadata.model_dump_json(indent=2)

        try:
            _wal_write(metadata, path)
        except Exception:
            failure_path = run_dir / "failure.json"
            failure_data = json.dumps(
                {"error": "Failed to write run.json", "traceback": traceback.format_exc()},
            )
            try:
                failure_path.write_text(failure_data, encoding="utf-8")
            except OSError:
                pass
            raise

        try:
            self.store.write(f"{run_id}/run.json", data)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Failed to write run.json to artifact store for run %s", run_id
            )

        return path

    def read_run_json(self, run_id: str) -> RunMetadata:
        """Read and validate the run.json metadata file."""
        content = self.read_artifact(run_id, "run.json")
        return RunMetadata.model_validate_json(content)

    def write_verdict(self, run_id: str, verdict: Verdict) -> None:
        """Write verdict.json and verdict.md to the run directory.

        Raises FileNotFoundError if the run directory does not exist.
        Raises ValueError if verdict.run_id does not match run_id.
        Dual-writes to the configured store (best-effort).
        """
        if verdict.run_id != run_id:
            raise ValueError(
                f"Verdict run_id {verdict.run_id!r} does not match target run {run_id!r}"
            )
        run_dir = self.run_dir(run_id)
        if not run_dir.exists():
            raise FileNotFoundError(f"Run directory not found: {run_dir}")

        json_data = verdict.model_dump_json(indent=2)
        json_path = run_dir / "verdict.json"
        json_path.write_text(json_data, encoding="utf-8")

        md_path = run_dir / "verdict.md"
        _write_verdict_markdown(md_path, verdict)

        try:
            self.store.write(f"{run_id}/verdict.json", json_data)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Failed to write verdict.json to artifact store for run %s", run_id
            )

    def write_experiment(self, run_id: str, experiment: Experiment) -> str:
        """Write the experiment.json file.

        Raises FileNotFoundError if the run directory does not exist.
        Raises ValueError if experiment.run_id does not match run_id.
        Dual-writes to the configured store (best-effort).
        """
        if experiment.run_id != run_id:
            raise ValueError(
                f"Experiment run_id {experiment.run_id!r} does not match target run {run_id!r}"
            )
        run_dir = self.run_dir(run_id)
        if not run_dir.exists():
            raise FileNotFoundError(f"Run directory not found: {run_dir}")

        return self._write_artifact_unchecked(
            run_id, "experiment.json", experiment.model_dump_json(indent=2)
        )

    def read_experiment(self, run_id: str) -> Experiment:
        """Read and validate the experiment.json file."""
        content = self.read_artifact(run_id, "experiment.json")
        return Experiment.model_validate_json(content)

    def ensure_run_exists(self, run_id: str) -> None:
        """Ensure the run directory and run.json metadata exist."""
        run_dir = self.run_dir(run_id)
        run_json = run_dir / "run.json"
        if not run_dir.exists() or not run_json.exists():
            raise FileNotFoundError(f"Run {run_id} does not exist in workspace {self.root}")


def _write_verdict_markdown(path: Path, verdict: Verdict) -> None:
    lines = [
        "# Verdict",
        "",
        f"- **Run ID:** {verdict.run_id}",
        f"- **Status:** {verdict.status}",
        f"- **Validation passed:** {verdict.validation_passed}",
        f"- **Apply succeeded:** {verdict.apply_succeeded}",
    ]
    if verdict.error_message is not None:
        lines.append(f"- **Error:** {verdict.error_message}")
    lines.extend(
        [
            f"- **Generated at:** {verdict.generated_at.isoformat()}",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
