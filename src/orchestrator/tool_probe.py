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
from pathlib import Path
from typing import Optional

from orchestrator.schemas.findings import ToolInfo

# cwd for the `-m` tool-version probe. `python -m` prepends the process's cwd
# to sys.path, so if the probe inherited the scanned repo's directory (e.g. a
# user running `patchforge scan .` from inside the target repo), a malicious
# `ruff.py`/`ruff/` at the repo root would shadow the real package. Pin the
# probe to a directory that is never the scanned target.
_PROBE_CWD = Path(tempfile.gettempdir())


def _probe_module(cmd: str, timeout: int = 10) -> Optional[ToolInfo]:
    """Probe *cmd* via ``sys.executable -m cmd --version``.

    Mirrors the validator's default invocation (see
    ``agents/validator/runners.py``). Returns ``None`` on any non-success
    outcome (non-zero exit, timeout, or OSError) — none of those prove the
    module is importable, so the caller must fall back to a PATH probe.

    Runs from :data:`_PROBE_CWD` with ``PYTHONPATH`` stripped so the probe
    cannot resolve *cmd* from the scanned repository instead of the real
    installed package (see :data:`_PROBE_CWD`'s docstring).
    """
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    try:
        res = subprocess.run(
            [sys.executable, "-m", cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_PROBE_CWD,
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
        version = raw.splitlines()[0] if res.returncode == 0 else None
        return ToolInfo(available=True, version=version)
    except (subprocess.TimeoutExpired, OSError):
        return ToolInfo(available=True, version=None)
