from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from orchestrator.agents.validator import run as run_validator
from orchestrator.git import apply_patch
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.git import GitCommandResult, ValidationWorkspace
from orchestrator.schemas.validator_output import ValidatorOutput

DEFAULT_IGNORE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "workspace",
    "node_modules",
}


def create_temp_copy(source: Path, ignore_dirs: list[str] | None = None) -> Path:
    ignore_set = set(ignore_dirs) if ignore_dirs is not None else DEFAULT_IGNORE_DIRS
    temp_dir = Path(tempfile.mkdtemp(prefix="val_"))

    shutil.copytree(
        source,
        temp_dir,
        ignore=lambda src, names: [n for n in names if n in ignore_set],
        dirs_exist_ok=True,
        symlinks=True,
    )
    return temp_dir


def apply_patch_to_copy(temp_root: Path, patch_path: Path) -> GitCommandResult:
    import subprocess
    if not (temp_root / ".git").exists():
        subprocess.run(["git", "init"], cwd=str(temp_root), capture_output=True, text=True)
    return apply_patch(temp_root, patch_path)


def run_validation_in_copy(temp_root: Path, config: TargetConfig) -> ValidatorOutput:
    val_config = config.model_copy(update={"target_path": temp_root})
    validator_output, _ = run_validator(config=val_config)
    return validator_output


def write_validation_json(workspace: ValidationWorkspace, results: ValidatorOutput) -> Path:
    dest_path = workspace.temporary_root / "validation.json"
    dest_path.write_text(results.model_dump_json(indent=2), encoding="utf-8")
    return dest_path


def cleanup_temp_copy(temp_root: Path) -> None:
    if temp_root.exists():
        shutil.rmtree(temp_root, ignore_errors=True)


@contextmanager
def create_validation_workspace(
    original_root: Path, patch_path: Path
) -> Generator[ValidationWorkspace, None, None]:
    temp_root = create_temp_copy(original_root)
    workspace = ValidationWorkspace(
        original_root=original_root,
        temporary_root=temp_root,
        patch_path=patch_path,
    )
    try:
        yield workspace
    finally:
        cleanup_temp_copy(temp_root)
