"""Shared CLI-tool availability probe for `scan` and `doctor`.

Extracted from :mod:`orchestrator.scanners.python` (issue #250) so `doctor`
can share the same detection strategy instead of maintaining an independent,
PATH-only implementation that silently disagrees with `scan` on venv-less
clones (see issue #252 / `docs/context/discoveries.md`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional

from orchestrator.schemas.findings import ToolInfo


def _probe_module(cmd: str, timeout: int = 10) -> Optional[ToolInfo]:
    """Probe *cmd* via ``sys.executable -m cmd --version``.

    Mirrors the validator's default invocation (see
    ``agents/validator/runners.py``). Returns ``None`` on any non-success
    outcome (non-zero exit, timeout, or OSError) — none of those prove the
    module is importable, so the caller must fall back to a PATH probe.

    Runs from a private, per-probe scratch directory (never the scanned
    repo, never the shared OS temp dir) with ``PYTHONPATH`` stripped.
    ``python -m`` prepends the process's cwd to ``sys.path``, so a scratch
    dir keeps the probe from resolving *cmd* via a malicious shadow module
    planted either in the scanned repo or in a world-writable shared temp
    dir (CWE-427; see issue #250 and issue #256).
    """
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    try:
        with tempfile.TemporaryDirectory(prefix="probe_", ignore_cleanup_errors=True) as probe_dir:
            res = subprocess.run(
                [sys.executable, "-m", cmd, "--version"],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=probe_dir,
                env=env,
            )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    raw = (res.stdout or res.stderr).strip()
    version = raw.splitlines()[0] if raw else None
    return ToolInfo(available=True, version=version)


def _probe_path(cmd: str, timeout: int = 10) -> ToolInfo:
    """Probe *cmd* via ``shutil.which`` plus a bare invocation.

    A ``which`` hit is treated as available regardless of the version probe
    outcome — this covers a ``cmd_override`` that relies on a PATH-installed
    binary rather than the validator's default ``-m`` form.
    """
    if shutil.which(cmd) is None:
        return ToolInfo(available=False)
    try:
        res = subprocess.run(
            [cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        raw = (res.stdout or res.stderr).strip()
        version = raw.splitlines()[0] if (res.returncode == 0 and raw) else None
        return ToolInfo(available=True, version=version)
    except (subprocess.TimeoutExpired, OSError):
        return ToolInfo(available=True, version=None)
