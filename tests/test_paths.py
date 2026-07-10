"""Tests for orchestrator.paths — PROJECT_ROOT resolution."""

import os
import subprocess
import sys
from pathlib import Path


def test_project_root_default_resolves_to_src():
    from orchestrator.paths import PROJECT_ROOT

    expected = Path(__file__).resolve().parent.parent / "src"
    assert expected == PROJECT_ROOT


def test_project_root_env_override(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from orchestrator.paths import PROJECT_ROOT; print(PROJECT_ROOT)",
        ],
        env={**os.environ, "PROJECT_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == str(tmp_path)
