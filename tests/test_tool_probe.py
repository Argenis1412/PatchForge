"""Regression tests for `orchestrator.tool_probe._probe_module`'s private
per-probe scratch directory (CWE-427 fix, issue #256)."""

import importlib.util
import subprocess
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrator.tool_probe import _probe_module


def test_probe_uses_private_cwd():
    """_probe_module runs from a private per-probe dir, not the shared
    gettempdir(), and cleans it up after returning. The happy path (a real
    available tool) still round-trips available=True/version correctly
    from that private cwd."""
    captured_cwd = []

    def spy_run(args, **kwargs):
        captured_cwd.append(Path(kwargs["cwd"]))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "ruff 0.1.0\n"
        result.stderr = ""
        return result

    with patch("orchestrator.tool_probe.subprocess.run", side_effect=spy_run):
        info = _probe_module("ruff")

    assert len(captured_cwd) == 1
    cwd = captured_cwd[0]
    assert cwd != Path(tempfile.gettempdir())
    assert cwd.name.startswith("probe_")
    assert not cwd.exists()

    assert info is not None
    assert info.available is True
    assert info.version == "ruff 0.1.0"


def test_shadow_module_not_executed(monkeypatch, tmp_path):
    """A malicious module planted in the shared temp dir must NOT be
    executed by _probe_module (CWE-427 regression).

    Redirects tempfile.gettempdir() to an isolated pytest tmp_path instead
    of touching the real OS-shared temp dir -- planting an executable .py
    file in the real shared temp dir during a test run would itself be the
    CWE-427 exposure this fix closes.
    """
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    shadow_name = f"_patchforge_shadow_{uuid.uuid4().hex}"
    assert importlib.util.find_spec(shadow_name) is None

    sentinel = tmp_path / "shadow_executed"
    shadow_code = f"import pathlib\npathlib.Path(r'{sentinel}').write_text('pwned')\n"
    (tmp_path / f"{shadow_name}.py").write_text(shadow_code)

    result = _probe_module(shadow_name)

    assert not sentinel.exists()
    assert result is None


def test_cleanup_on_timeout():
    """Private per-probe dir is cleaned up even when the probe times out."""
    captured_cwd = []

    def timeout_run(args, **kwargs):
        captured_cwd.append(Path(kwargs["cwd"]))
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    with patch("orchestrator.tool_probe.subprocess.run", side_effect=timeout_run):
        result = _probe_module("ruff", timeout=1)

    assert result is None
    assert len(captured_cwd) == 1
    assert not captured_cwd[0].exists()


def test_creation_failure_returns_none():
    """A scratch-dir creation failure degrades to 'tool unavailable'
    instead of crashing scan/doctor with an unhandled exception.

    Patches the TemporaryDirectory constructor itself (not __enter__):
    the real mkdtemp() call that can genuinely raise OSError (disk full,
    permissions) happens inside __init__, before __enter__/__exit__ ever
    run -- patching __enter__ instead would let a real directory get
    created and never cleaned up (since __exit__ only runs if __enter__
    succeeds), while also not exercising the actual failure mode.
    """
    with (
        patch(
            "orchestrator.tool_probe.tempfile.TemporaryDirectory",
            side_effect=OSError("disk full"),
        ),
        patch("orchestrator.tool_probe.subprocess.run") as mock_run,
    ):
        result = _probe_module("ruff")

    assert result is None
    mock_run.assert_not_called()
