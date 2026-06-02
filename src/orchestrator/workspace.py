from __future__ import annotations

import json
from pathlib import Path


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
