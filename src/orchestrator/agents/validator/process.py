"""Shared preparation of validator subprocesses.

This module intentionally prepares commands only. Later validator adapters and
``doctor`` will consume the same representation so they do not reimplement
working-directory or virtual-environment PATH semantics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class PreparedProcess:
    """A command ready for ``subprocess.run`` without shell interpretation."""

    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] | None


def build_venv_environment(project_root: Path) -> dict[str, str] | None:
    """Return an environment preferring the target's virtual environment."""
    for subdir in ("bin", "Scripts"):
        venv_bin = project_root / ".venv" / subdir
        if venv_bin.is_dir():
            env = os.environ.copy()
            env["PATH"] = str(venv_bin) + os.pathsep + env.get("PATH", "")
            return env
    return None


def prepare_process(
    command: list[str],
    cwd: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> PreparedProcess:
    """Validate and freeze argv, cwd and environment for a validator process."""
    if not command or not all(isinstance(arg, str) and arg for arg in command):
        raise ValueError("Validator command must contain one or more non-empty arguments")
    frozen_environment = MappingProxyType(dict(environment)) if environment is not None else None
    return PreparedProcess(argv=tuple(command), cwd=Path(cwd), env=frozen_environment)
