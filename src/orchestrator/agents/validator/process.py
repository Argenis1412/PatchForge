"""Shared preparation of validator subprocesses.

This module intentionally prepares commands only. Later validator adapters and
``doctor`` will consume the same representation so they do not reimplement
working-directory or virtual-environment PATH semantics.
"""

from __future__ import annotations

import os
import signal
import subprocess
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


@dataclass(frozen=True)
class ProcessResult:
    """Raw operational result, deliberately separate from validation policy."""

    return_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    unavailable: bool = False
    cleanup_failed: bool = False


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


def _terminate_process_tree(process: subprocess.Popen[str]) -> bool:
    """Terminate the managed process tree and report whether it exited."""
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if completed.returncode != 0:
                return False
        else:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                return False
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
        return process.returncode is not None
    except (OSError, subprocess.SubprocessError):
        return False


def execute_process(prepared: PreparedProcess, timeout: int) -> ProcessResult:
    """Run a prepared command with managed-tree timeout cleanup.

    This returns operational facts only. Adapter aggregation decides whether a
    return code is approved for a particular validator declaration.
    """
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            prepared.argv,
            cwd=str(prepared.cwd),
            env=prepared.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
    except FileNotFoundError:
        return ProcessResult(return_code=None, unavailable=True)

    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return ProcessResult(return_code=process.returncode, stdout=stdout, stderr=stderr)
    except subprocess.TimeoutExpired:
        cleaned = _terminate_process_tree(process)
        return ProcessResult(timed_out=True, cleanup_failed=not cleaned, return_code=None)
