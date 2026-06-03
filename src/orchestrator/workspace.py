from __future__ import annotations

import json
from pathlib import Path

from orchestrator.schemas.artifacts import RunMetadata


class WorkspaceManager:
    def __init__(self, workspace_path: Path):
        self.root = Path(workspace_path).resolve()
        self.runs = self.root / "runs"
        self.logs = self.root / "logs"
        self.prompts = self.root / "prompts"
        self.outputs = self.root / "outputs"
        self.cache = self.root / "cache"
        self.temp = self.root / "temp"
        self.manifest = self.outputs / "manifest.json"

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
        """Get the runs/{run_id} directory path."""
        return self.runs / run_id

    def create_run_directory(self, run_id: str) -> Path:
        """Create and return the runs/{run_id} directory."""
        path = self.run_dir(run_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_artifact(self, run_id: str, filename: str, content: str) -> Path:
        """Write content to an artifact file in the run directory."""
        run_dir = self.create_run_directory(run_id)
        path = run_dir / filename
        path.write_text(content, encoding="utf-8")
        return path

    def read_artifact(self, run_id: str, filename: str) -> str:
        """Read content from an artifact file in the run directory."""
        path = self.run_dir(run_id) / filename
        if not path.exists():
            raise FileNotFoundError(f"Artifact {filename} not found for run {run_id} in {path}")
        return path.read_text(encoding="utf-8")

    def write_run_json(self, run_id: str, metadata: RunMetadata) -> Path:
        """Write the run.json metadata file."""
        return self.write_artifact(run_id, "run.json", metadata.model_dump_json(indent=2))

    def read_run_json(self, run_id: str) -> RunMetadata:
        """Read and validate the run.json metadata file."""
        content = self.read_artifact(run_id, "run.json")
        return RunMetadata.model_validate_json(content)

    def ensure_run_exists(self, run_id: str) -> None:
        """Ensure the run directory and run.json metadata exist."""
        run_dir = self.run_dir(run_id)
        run_json = run_dir / "run.json"
        if not run_dir.exists() or not run_json.exists():
            raise FileNotFoundError(f"Run {run_id} does not exist in workspace {self.root}")

