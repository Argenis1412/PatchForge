from __future__ import annotations

import json
import os
import re
from pathlib import Path

from orchestrator.safety import validate_filename
from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.schemas.experiment import Experiment, Verdict
from orchestrator.storage import _wal_write

# Only allow alphanumeric characters, underscores, and hyphens in run IDs.
_RUN_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_run_id(run_id: str) -> None:
    """Raise ValueError if run_id is empty or contains path-traversal characters."""
    if not run_id or not _RUN_ID_RE.match(run_id):
        raise ValueError(
            f"Invalid run_id {run_id!r}. "
            "Only alphanumeric characters, underscores, and hyphens are allowed."
        )


class WorkspaceManager:
    def __init__(self, workspace_path: Path | None = None, worker_id: str = ""):
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
            self.root = self.root / worker_id
        self.runs = self.root / "runs"
        self.logs = self.root / "logs"
        self.prompts = self.root / "prompts"
        self.outputs = self.root / "outputs"
        self.cache = self.root / "cache"
        self.temp = self.root / "temp"
        self.manifest = self.outputs / "manifest.json"
        self._worker_id = worker_id

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

    def staging_dir_for_run(self, run_id: str) -> Path:
        path = self.outputs / "staging" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup_stale_workspaces(self, max_age_hours: int = 24) -> None:
        import shutil
        import time

        now = time.time()
        cutoff = now - (max_age_hours * 3600)
        for child in self.root.parent.iterdir():
            if child.is_dir() and child.name.startswith("worker-"):
                if child.stat().st_mtime < cutoff:
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

    def write_artifact(self, run_id: str, filename: str, content: str) -> Path:
        """Write content to an artifact file in an *existing* run directory.

        Raises FileNotFoundError if the run does not exist yet — callers must
        call create_run_directory (i.e. scan) before writing any artifact.
        """
        self.ensure_run_exists(run_id)
        run_dir = self.run_dir(run_id)
        path = run_dir / validate_filename(filename)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_artifact_unchecked(self, run_id: str, filename: str, content: str) -> Path:
        """Write to an artifact path without requiring the run to pre-exist.

        Used exclusively by write_run_json during the initial creation sequence
        inside scan (after create_run_directory but before the first run.json exists).
        """
        run_dir = self.run_dir(run_id)
        path = run_dir / validate_filename(filename)
        path.write_text(content, encoding="utf-8")
        return path

    def read_artifact(self, run_id: str, filename: str) -> str:
        """Read content from an artifact file in the run directory."""
        path = self.run_dir(run_id) / validate_filename(filename)
        if not path.exists():
            raise FileNotFoundError(f"Artifact {filename} not found for run {run_id} in {path}")
        return path.read_text(encoding="utf-8")

    def write_run_json(self, run_id: str, metadata: RunMetadata) -> Path:
        """Write the run.json metadata file.

        Uses the unchecked writer so it can be called both during initial
        creation (scan) and during subsequent status updates.
        """
        import traceback

        run_dir = self.run_dir(run_id)
        path = run_dir / "run.json"

        try:
            _wal_write(metadata, path)
            return path
        except Exception:
            failure_path = run_dir / "failure.json"
            failure_data = json.dumps(
                {"error": "Failed to write run.json", "traceback": traceback.format_exc()},
            )
            try:
                failure_path.write_text(failure_data, encoding="utf-8")
            except OSError:
                pass  # best-effort write; don't mask the original error
            raise

    def read_run_json(self, run_id: str) -> RunMetadata:
        """Read and validate the run.json metadata file."""
        content = self.read_artifact(run_id, "run.json")
        return RunMetadata.model_validate_json(content)

    def write_verdict(self, run_id: str, verdict: Verdict) -> None:
        """Write verdict.json and verdict.md to the run directory.

        Raises FileNotFoundError if the run directory does not exist.
        Raises ValueError if verdict.run_id does not match run_id.
        """
        if verdict.run_id != run_id:
            raise ValueError(
                f"Verdict run_id {verdict.run_id!r} does not match target run {run_id!r}"
            )
        run_dir = self.run_dir(run_id)
        if not run_dir.exists():
            raise FileNotFoundError(f"Run directory not found: {run_dir}")

        json_path = run_dir / "verdict.json"
        json_path.write_text(verdict.model_dump_json(indent=2), encoding="utf-8")

        md_path = run_dir / "verdict.md"
        _write_verdict_markdown(md_path, verdict)

    def write_experiment(self, run_id: str, experiment: Experiment) -> Path:
        """Write the experiment.json file.

        Raises FileNotFoundError if the run directory does not exist.
        Raises ValueError if experiment.run_id does not match run_id.
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
