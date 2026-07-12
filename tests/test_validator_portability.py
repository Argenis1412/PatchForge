"""Tests for issue #155: venv PATH discovery and ignore_dirs forwarding."""

import os
import subprocess
from unittest.mock import patch

import pytest

from orchestrator.agents.validator.runners import (
    _build_env_with_venv,
    run_pytest,
    run_ruff,
    run_tsc,
)

# ---------------------------------------------------------------------------
# _build_env_with_venv
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_env_venv_bin_posix(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    env = _build_env_with_venv(tmp_path)
    assert env is not None
    assert str(venv_bin) in env["PATH"]
    assert env["PATH"].startswith(str(venv_bin))


@pytest.mark.unit
def test_build_env_venv_scripts_windows(tmp_path):
    venv_scripts = tmp_path / ".venv" / "Scripts"
    venv_scripts.mkdir(parents=True)
    env = _build_env_with_venv(tmp_path)
    assert env is not None
    assert str(venv_scripts) in env["PATH"]
    assert env["PATH"].startswith(str(venv_scripts))


@pytest.mark.unit
def test_build_env_no_venv_returns_none(tmp_path):
    assert _build_env_with_venv(tmp_path) is None


@pytest.mark.unit
def test_build_env_preserves_existing_path(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    env = _build_env_with_venv(tmp_path)
    assert env is not None
    original_path = os.environ.get("PATH", "")
    assert original_path in env["PATH"]


# ---------------------------------------------------------------------------
# run_ruff — ignore_dirs forwarding
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_ruff_extend_exclude_per_dir(tmp_path):
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_ruff("r1", tmp_path, ignore_dirs=["node_modules", "dist"])

    assert "--extend-exclude=node_modules" in captured["cmd"]
    assert "--extend-exclude=dist" in captured["cmd"]


@pytest.mark.unit
def test_run_ruff_no_extend_exclude_when_ignore_dirs_empty(tmp_path):
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_ruff("r1", tmp_path, ignore_dirs=[])

    assert not any("--extend-exclude" in arg for arg in captured["cmd"])


@pytest.mark.unit
def test_run_ruff_no_extend_exclude_in_staging_mode(tmp_path):
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    staged_file = staging_dir / "foo.py"
    staged_file.write_text("x = 1\n")

    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_ruff("r1", tmp_path, staging_dir=staging_dir, ignore_dirs=["node_modules"])

    assert not any("--extend-exclude" in arg for arg in captured["cmd"])
    assert str(staged_file) in captured["cmd"]


# ---------------------------------------------------------------------------
# run_pytest — ignore_dirs forwarding
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_pytest_ignore_per_dir(tmp_path):
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_pytest("r1", tmp_path, ignore_dirs=["node_modules", "dist"])

    assert "--ignore=node_modules" in captured["cmd"]
    assert "--ignore=dist" in captured["cmd"]


@pytest.mark.unit
def test_run_pytest_no_ignore_when_ignore_dirs_empty(tmp_path):
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_pytest("r1", tmp_path, ignore_dirs=[])

    assert not any("--ignore=" in arg for arg in captured["cmd"])


# ---------------------------------------------------------------------------
# venv env injection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_ruff_no_venv_injection_with_default_cmd(tmp_path):
    """Default cmd uses sys.executable (absolute), so venv injection is skipped."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_ruff("r1", tmp_path)

    assert captured["env"] is None


@pytest.mark.unit
def test_run_ruff_injects_venv_env_bare_cmd_override(tmp_path):
    """A bare (non-absolute) cmd_override still gets venv injection."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_ruff("r1", tmp_path, cmd_override=["ruff", "check", "."])

    assert captured["env"] is not None
    assert str(venv_bin) in captured["env"]["PATH"]


@pytest.mark.unit
def test_run_ruff_no_venv_injection_with_absolute_cmd(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    abs_cmd = [str(tmp_path / "bin" / "ruff"), "check", "."]
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_ruff("r1", tmp_path, cmd_override=abs_cmd)

    assert captured["env"] is None


@pytest.mark.unit
def test_run_pytest_no_venv_injection_with_default_cmd(tmp_path):
    """Default cmd uses sys.executable (absolute), so venv injection is skipped."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_pytest("r1", tmp_path)

    assert captured["env"] is None


@pytest.mark.unit
def test_run_pytest_injects_venv_env_bare_cmd_override(tmp_path):
    """A bare (non-absolute) cmd_override still gets venv injection."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_pytest("r1", tmp_path, cmd_override=["pytest", ".", "--tb=short", "-q"])

    assert captured["env"] is not None
    assert str(venv_bin) in captured["env"]["PATH"]


@pytest.mark.unit
def test_run_pytest_no_venv_injection_with_absolute_cmd(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    abs_cmd = [str(tmp_path / "bin" / "pytest"), ".", "--tb=short", "-q"]
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_pytest("r1", tmp_path, cmd_override=abs_cmd)

    assert captured["env"] is None


# ---------------------------------------------------------------------------
# run_ruff — staging path + venv injection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_ruff_staging_no_venv_injection_with_default_cmd(tmp_path):
    """sys.executable is absolute, so venv injection is skipped in staging."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    (staging_dir / "foo.py").write_text("x = 1\n")
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_ruff("r1", tmp_path, staging_dir=staging_dir)

    assert captured["env"] is None


# ---------------------------------------------------------------------------
# run_pytest — overlay path with ignore_dirs and venv
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_pytest_overlay_forwards_ignore_dirs(tmp_path):
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    (staging_dir / "foo.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("")
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_pytest("r1", tmp_path, staging_dir=staging_dir, ignore_dirs=["dist", "vendor"])

    assert "--ignore=dist" in captured["cmd"]
    assert "--ignore=vendor" in captured["cmd"]


@pytest.mark.unit
def test_run_pytest_overlay_no_venv_injection_with_default_cmd(tmp_path):
    """sys.executable is absolute, so venv injection is skipped in overlay."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    (staging_dir / "foo.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("")
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_pytest("r1", tmp_path, staging_dir=staging_dir)

    assert captured["env"] is None


# ---------------------------------------------------------------------------
# run_tsc — venv injection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_tsc_injects_venv_env_bare_cmd(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}")
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_tsc("r1", tmp_path)

    assert captured["env"] is not None
    assert str(venv_bin) in captured["env"]["PATH"]


@pytest.mark.unit
def test_run_tsc_no_venv_injection_with_absolute_cmd(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}")
    abs_cmd = [str(tmp_path / "bin" / "tsc"), "--noEmit"]
    captured = {}

    def fake_run(cmd, cwd, capture_output, text, timeout, env=None):
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        run_tsc("r1", tmp_path, cmd_override=abs_cmd)

    assert captured["env"] is None
