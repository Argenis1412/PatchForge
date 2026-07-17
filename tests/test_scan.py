"""Tests for the deterministic V1 scanner (Issue #45).

Covers:
- Scanner unit tests (orchestrator.scanners.python.scan)
- CLI integration tests (orchestrator scan <path>)
- plan guard when V1 findings are present
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from orchestrator.main import app
from orchestrator.scanners.python import scan
from orchestrator.schemas.findings import ScanFindings

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    """Initialise a git repo with a single initial commit at *path*."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("repo\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _make_pyproject(path: Path) -> None:
    """Write a minimal valid pyproject.toml to *path*."""
    (path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    )


def _make_full_valid_repo(path: Path) -> None:
    """Create a git repo that satisfies all V1 requirements."""
    _init_git_repo(path)
    _make_pyproject(path)
    (path / "tests").mkdir()
    (path / "tests" / "__init__.py").write_text("")
    (path / "tests" / "test_example.py").write_text("def test_ok(): pass\n")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def valid_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_full_valid_repo(repo)
    return repo


@pytest.fixture()
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# ---------------------------------------------------------------------------
# Helpers: patch only within the scanner module to avoid recursion
#
# We patch:
#   orchestrator.scanners.python.shutil.which  — tool availability
#   orchestrator.scanners.python.subprocess.run — tool version string
#
# This leaves orchestrator.git's subprocess.run untouched so real git
# commands work normally in tests.
# ---------------------------------------------------------------------------


def _mock_which(cmd: str) -> str | None:
    if cmd in ("ruff", "pytest"):
        return f"/usr/bin/{cmd}"
    return None


def _mock_tool_run(args, **kwargs):
    """Return a fake CompletedProcess for ruff/pytest --version calls.

    *args* is the argv list passed to ``subprocess.run``. Handles both the
    module form (``[sys.executable, "-m", cmd, "--version"]``) and the bare
    PATH form (``[cmd, "--version"]``) so the reported tool name is correct
    regardless of which probe fired.
    """
    from unittest.mock import MagicMock

    cmd = args[2] if args and len(args) > 2 and args[1] == "-m" else (args[0] if args else "tool")
    result = MagicMock()
    result.returncode = 0
    result.stdout = f"{cmd} 1.0.0\n"
    result.stderr = ""
    return result


# Convenience context manager helpers used across tests.
_SCANNER_PATCHES = (
    "orchestrator.scanners.python.shutil.which",
    "orchestrator.scanners.python.subprocess.run",
)


def _make_module_miss_run(absent_cmd: str):
    """Build a ``subprocess.run`` side_effect where *absent_cmd*'s module
    probe (``sys.executable -m absent_cmd --version``) fails, while every
    other probe (module or bare) succeeds via :func:`_mock_tool_run`.

    Simulates a tool that is neither importable nor on PATH, forcing
    ``_detect_tool`` through both probes to a genuine miss.
    """
    from unittest.mock import MagicMock

    def _run(args, **kwargs):
        if args and len(args) > 2 and args[1] == "-m" and args[2] == absent_cmd:
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = f"No module named {absent_cmd}"
            return result
        return _mock_tool_run(args, **kwargs)

    return _run


# ---------------------------------------------------------------------------
# 1. test_deterministic_scanner_full_findings
# ---------------------------------------------------------------------------


def test_deterministic_scanner_full_findings(valid_repo: Path):
    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        findings = scan(valid_repo)

    assert isinstance(findings, ScanFindings)
    assert findings.repository_root != ""
    assert isinstance(findings.base_commit, str)
    assert isinstance(findings.branch, str)
    assert isinstance(findings.v1_supported, bool)
    assert isinstance(findings.support_reasons, list)
    assert isinstance(findings.unsupported_reasons, list)
    assert findings.pyproject.exists is True
    assert findings.pyproject.valid is True
    assert findings.ruff.available is True
    assert findings.pytest.available is True
    assert findings.test_suite.detected is True
    assert isinstance(findings.total_python_files, int)
    assert isinstance(findings.packages, list)
    assert isinstance(findings.modules, list)
    assert isinstance(findings.hotspots, list)


# ---------------------------------------------------------------------------
# 2. test_scan_cli_creates_findings
# ---------------------------------------------------------------------------


def test_scan_cli_creates_findings(valid_repo: Path, workspace_dir: Path):
    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        result = runner.invoke(
            app,
            ["scan", str(valid_repo), "--workspace", str(workspace_dir)],
        )

    assert result.exit_code == 0, result.output
    assert "Scanner completed successfully!" in result.output

    runs_dir = workspace_dir / "runs"
    assert runs_dir.exists()
    run_dirs = list(runs_dir.iterdir())
    assert len(run_dirs) == 1

    run_id = run_dirs[0].name
    findings_path = runs_dir / run_id / "findings.json"
    assert findings_path.exists()

    data = json.loads(findings_path.read_text())
    assert data["v1_supported"] is True

    # AC9: no plan.json or patch.diff generated by scan
    assert not (runs_dir / run_id / "plan.json").exists()
    assert not (runs_dir / run_id / "patch.diff").exists()


# ---------------------------------------------------------------------------
# 3. test_scan_fails_without_pyproject
# ---------------------------------------------------------------------------


def test_scan_fails_without_pyproject(tmp_path: Path, workspace_dir: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "tests").mkdir()

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        result = runner.invoke(
            app,
            ["scan", str(repo), "--workspace", str(workspace_dir)],
        )

    assert result.exit_code == 1

    # AC8: findings.json written before exit
    runs_dir = workspace_dir / "runs"
    run_dirs = list(runs_dir.iterdir())
    assert len(run_dirs) == 1
    findings_path = runs_dir / run_dirs[0].name / "findings.json"
    assert findings_path.exists()
    data = json.loads(findings_path.read_text())
    assert data["v1_supported"] is False
    assert any("pyproject" in r.lower() for r in data["unsupported_reasons"])


# ---------------------------------------------------------------------------
# 4. test_scan_fails_without_ruff
# ---------------------------------------------------------------------------


def test_scan_fails_without_ruff(valid_repo: Path, workspace_dir: Path):
    def _which_no_ruff(cmd: str) -> str | None:
        if cmd == "pytest":
            return "/usr/bin/pytest"
        return None

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_which_no_ruff),
        patch(
            "orchestrator.scanners.python.subprocess.run",
            side_effect=_make_module_miss_run("ruff"),
        ),
    ):
        result = runner.invoke(
            app,
            ["scan", str(valid_repo), "--workspace", str(workspace_dir)],
        )

    assert result.exit_code == 1
    runs_dir = workspace_dir / "runs"
    findings_path = runs_dir / list(runs_dir.iterdir())[0].name / "findings.json"
    data = json.loads(findings_path.read_text())
    assert data["v1_supported"] is False
    assert any("ruff" in r.lower() for r in data["unsupported_reasons"])


# ---------------------------------------------------------------------------
# 5. test_scan_fails_without_pytest
# ---------------------------------------------------------------------------


def test_scan_fails_without_pytest(valid_repo: Path, workspace_dir: Path):
    def _which_no_pytest(cmd: str) -> str | None:
        if cmd == "ruff":
            return "/usr/bin/ruff"
        return None

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_which_no_pytest),
        patch(
            "orchestrator.scanners.python.subprocess.run",
            side_effect=_make_module_miss_run("pytest"),
        ),
    ):
        result = runner.invoke(
            app,
            ["scan", str(valid_repo), "--workspace", str(workspace_dir)],
        )

    assert result.exit_code == 1
    runs_dir = workspace_dir / "runs"
    findings_path = runs_dir / list(runs_dir.iterdir())[0].name / "findings.json"
    data = json.loads(findings_path.read_text())
    assert data["v1_supported"] is False
    assert any("pytest" in r.lower() for r in data["unsupported_reasons"])


# ---------------------------------------------------------------------------
# 6. test_scan_fails_without_test_suite
# ---------------------------------------------------------------------------


def test_scan_fails_without_test_suite(tmp_path: Path, workspace_dir: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _make_pyproject(repo)
    # No tests/ directory, no conftest.py, no test_*.py files

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        result = runner.invoke(
            app,
            ["scan", str(repo), "--workspace", str(workspace_dir)],
        )

    assert result.exit_code == 1
    runs_dir = workspace_dir / "runs"
    findings_path = runs_dir / list(runs_dir.iterdir())[0].name / "findings.json"
    data = json.loads(findings_path.read_text())
    assert data["v1_supported"] is False
    assert any("test suite" in r.lower() for r in data["unsupported_reasons"])


# ---------------------------------------------------------------------------
# 7. test_scan_does_not_touch_target (AC5)
# ---------------------------------------------------------------------------


def test_scan_does_not_touch_target(valid_repo: Path, workspace_dir: Path):
    before = {p.name for p in valid_repo.iterdir()}

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        runner.invoke(
            app,
            ["scan", str(valid_repo), "--workspace", str(workspace_dir)],
        )

    after = {p.name for p in valid_repo.iterdir()}
    assert before == after, (
        f"Scan modified target repo: added={after - before}, removed={before - after}"
    )


# ---------------------------------------------------------------------------
# 8. test_hotspots_detected
# ---------------------------------------------------------------------------


def test_hotspots_detected(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    # Create a file with many lines
    big_file = repo / "big.py"
    big_file.write_text("\n".join(f"x_{i} = {i}" for i in range(200)))

    # Create a file with many definitions
    defs_file = repo / "defs.py"
    defs_file.write_text(
        "\n".join(f"def func_{i}(): pass" for i in range(20))
        + "\n"
        + "\n".join(f"class Cls_{i}: pass" for i in range(10))
    )

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        findings = scan(repo)

    file_names = [h.file for h in findings.hotspots]
    assert any("big.py" in f for f in file_names), "big.py missing from hotspots"
    assert any("defs.py" in f for f in file_names), "defs.py missing from hotspots"

    largest = [h for h in findings.hotspots if h.reason == "largest_file"]
    assert any("big.py" in h.file for h in largest)

    most_defs = [h for h in findings.hotspots if h.reason == "most_definitions"]
    assert any("defs.py" in h.file for h in most_defs)


# ---------------------------------------------------------------------------
# 9. test_typescript_no_effect_on_v1 (AC6)
# ---------------------------------------------------------------------------


def test_typescript_no_effect_on_v1(valid_repo: Path):
    (valid_repo / "app.ts").write_text("export const x = 1;\n")

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        findings = scan(valid_repo)

    assert findings.v1_supported is True, f"Unsupported reasons: {findings.unsupported_reasons}"
    assert any("TypeScript" in r for r in findings.support_reasons)


# ---------------------------------------------------------------------------
# 10. test_deterministic_scanner_no_api_keys (AC2 / AC3)
# ---------------------------------------------------------------------------


def test_deterministic_scanner_no_api_keys(valid_repo: Path):
    """scan must work even when all AI API key env vars are absent."""
    env_keys = ["GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"]
    cleaned_env = {k: v for k, v in os.environ.items() if k not in env_keys}

    with (
        patch.dict(os.environ, cleaned_env, clear=True),
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        findings = scan(valid_repo)

    assert isinstance(findings, ScanFindings)


# ---------------------------------------------------------------------------
# 11. test_scan_with_empty_repo_no_commits
# ---------------------------------------------------------------------------


def test_scan_with_empty_repo_no_commits(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True
    )
    # No commits — git rev-parse HEAD will fail; current_head() returns "".
    # We patch _detect_tool to avoid subprocess entirely (avoids the shared
    # subprocess module-singleton side-effect on git calls).
    from orchestrator.schemas.findings import ToolInfo as _ToolInfo

    mock_tool = _ToolInfo(available=True, version="1.0.0")

    with patch("orchestrator.scanners.python._detect_tool", return_value=mock_tool):
        findings = scan(repo)

    assert findings.base_commit == "", f"Expected empty base_commit, got {findings.base_commit!r}"


# ---------------------------------------------------------------------------
# 12. test_scanner_handles_syntax_error_in_file
# ---------------------------------------------------------------------------


def test_scanner_handles_syntax_error_in_file(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    # Write an intentionally broken Python file
    bad_file = repo / "broken.py"
    bad_file.write_text("def foo(:\n    pass\n")

    # Also write a valid file to ensure scanning continues
    good_file = repo / "good.py"
    good_file.write_text("def bar(): pass\n")

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        findings = scan(repo)

    # Scanner must not raise; both files should appear in modules
    module_names = [m.split("/")[-1] for m in findings.modules]
    assert "broken.py" in module_names
    assert "good.py" in module_names

    # broken.py should not appear as a most_definitions hotspot with value > 0
    broken_defs = [
        h for h in findings.hotspots if "broken.py" in h.file and h.reason == "most_definitions"
    ]
    for h in broken_defs:
        assert (h.value or 0) == 0


# ---------------------------------------------------------------------------
# 13. test_scan_writes_run_json_on_scanner_failure
# ---------------------------------------------------------------------------


def test_scan_writes_run_json_on_scanner_failure(valid_repo: Path, workspace_dir: Path):
    """When scan() raises, run.json with status=failed must still be written."""
    with patch("orchestrator.commands.scan.scan", side_effect=RuntimeError("boom")):
        result = runner.invoke(
            app,
            ["scan", str(valid_repo), "--workspace", str(workspace_dir)],
        )

    assert result.exit_code == 1

    runs_dir = workspace_dir / "runs"
    run_dirs = list(runs_dir.iterdir())
    assert len(run_dirs) == 1
    run_json_path = runs_dir / run_dirs[0].name / "run.json"
    assert run_json_path.exists()
    data = json.loads(run_json_path.read_text())
    assert data["status"] == "failed"
    assert data["run_id"] == run_dirs[0].name


# ---------------------------------------------------------------------------
# 14. test_plan_errors_on_v1_findings
# ---------------------------------------------------------------------------


def test_plan_errors_on_v1_findings(valid_repo: Path, workspace_dir: Path):
    """Running plan on a V1 scan run must print a clear error and exit 1."""
    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        scan_res = runner.invoke(
            app,
            ["scan", str(valid_repo), "--workspace", str(workspace_dir)],
        )
    assert scan_res.exit_code == 0, scan_res.output

    run_id = list((workspace_dir / "runs").iterdir())[0].name

    plan_res = runner.invoke(
        app,
        ["plan", run_id, "--workspace", str(workspace_dir)],
    )
    assert plan_res.exit_code == 1
    assert "V1 deterministic scan" in plan_res.output
    assert "plan" in plan_res.output.lower()

    # run.json status should be updated to "failed"
    run_data = json.loads((workspace_dir / "runs" / run_id / "run.json").read_text())
    assert run_data["status"] == "failed"


# ---------------------------------------------------------------------------
# 15. test_scan_default_risk_budget
# ---------------------------------------------------------------------------


def test_scan_default_risk_budget(valid_repo: Path, workspace_dir: Path):
    """Default scan writes risk_budget='low' and max_files=2."""
    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        result = runner.invoke(
            app,
            ["scan", str(valid_repo), "--workspace", str(workspace_dir)],
        )

    assert result.exit_code == 0, result.output

    runs_dir = workspace_dir / "runs"
    run_id = list(runs_dir.iterdir())[0].name
    run_json_path = runs_dir / run_id / "run.json"
    assert run_json_path.exists()

    run_data = json.loads(run_json_path.read_text())
    assert run_data["risk_budget"] == "low"
    assert run_data["max_files"] == 2


# ---------------------------------------------------------------------------
# 16. test_scan_medium_risk_budget
# ---------------------------------------------------------------------------


def test_scan_medium_risk_budget(valid_repo: Path, workspace_dir: Path):
    """Scan with --risk-budget medium writes risk_budget='medium'."""
    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        result = runner.invoke(
            app,
            [
                "scan",
                str(valid_repo),
                "--workspace",
                str(workspace_dir),
                "--risk-budget",
                "medium",
            ],
        )

    assert result.exit_code == 0, result.output

    runs_dir = workspace_dir / "runs"
    run_id = list(runs_dir.iterdir())[0].name
    run_json_path = runs_dir / run_id / "run.json"
    assert run_json_path.exists()

    run_data = json.loads(run_json_path.read_text())
    assert run_data["risk_budget"] == "medium"
    assert run_data["max_files"] == 5
    assert run_data["max_diff_lines"] == 250


# ---------------------------------------------------------------------------
# 17. test_scan_invalid_risk_budget
# ---------------------------------------------------------------------------


def test_scan_invalid_risk_budget(valid_repo: Path, workspace_dir: Path):
    """Invalid --risk-budget value exits with error listing valid options."""
    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_mock_tool_run),
    ):
        result = runner.invoke(
            app,
            [
                "scan",
                str(valid_repo),
                "--workspace",
                str(workspace_dir),
                "--risk-budget",
                "invalid",
            ],
        )

    assert result.exit_code == 1
    assert "Invalid value for --risk-budget" in result.output
    assert "low" in result.output
    assert "medium" in result.output
    assert "high" in result.output


# ---------------------------------------------------------------------------
# 17. Approval Provenance (#241)
# ---------------------------------------------------------------------------


_REAL_SUBPROCESS_RUN = subprocess.run


def _mock_tool_run_pass_through_git(args, **kwargs):
    """Like _mock_tool_run but delegates 'git ...' commands to the real
    binary so provenance capture (git config --get user.*) works. Used by
    the scan-level provenance tests, which need git config to return real
    values from the tmp_path fixture rather than the '$cmd 1.0.0' stub.

    Uses a pre-captured reference to subprocess.run so the delegation does
    not re-enter the mock and infinite-recurse."""
    cmd = args[0] if args else "tool"
    if cmd == "git":
        return _REAL_SUBPROCESS_RUN(args, **kwargs)
    return _mock_tool_run(args, **kwargs)


def test_scan_captures_triggered_by_from_github_actor(
    valid_repo: Path, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """scan.py must read GITHUB_ACTOR when running in CI."""
    monkeypatch.setenv("GITHUB_ACTOR", "octocat")

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch(
            "orchestrator.scanners.python.subprocess.run",
            side_effect=_mock_tool_run_pass_through_git,
        ),
    ):
        result = runner.invoke(app, ["scan", str(valid_repo), "--workspace", str(workspace_dir)])

    assert result.exit_code == 0, result.output
    runs_dir = workspace_dir / "runs"
    run_dirs = list(runs_dir.iterdir())
    data = json.loads((runs_dir / run_dirs[0].name / "run.json").read_text())
    assert data["triggered_by"] == "github:octocat"


def test_scan_captures_triggered_by_from_local_git_config(
    valid_repo: Path, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """scan.py must fall back to local git config when GITHUB_ACTOR is unset.

    valid_repo is initialised with user.name='Test' / user.email='t@t.com'
    (see _init_git_repo)."""
    monkeypatch.delenv("GITHUB_ACTOR", raising=False)

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_mock_which),
        patch(
            "orchestrator.scanners.python.subprocess.run",
            side_effect=_mock_tool_run_pass_through_git,
        ),
    ):
        result = runner.invoke(app, ["scan", str(valid_repo), "--workspace", str(workspace_dir)])

    assert result.exit_code == 0, result.output
    runs_dir = workspace_dir / "runs"
    run_dirs = list(runs_dir.iterdir())
    data = json.loads((runs_dir / run_dirs[0].name / "run.json").read_text())
    assert data["triggered_by"] == "local:Test <t@t.com>"


def test_scan_failure_path_carries_triggered_by(
    valid_repo: Path, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Symmetry with ci.py's _fail() closure: even a scanner crash must
    persist triggered_by on the minimal run.json — failed runs must be
    just as auditable as successful ones."""
    monkeypatch.setenv("GITHUB_ACTOR", "octocat")

    with patch("orchestrator.commands.scan.scan", side_effect=RuntimeError("boom")):
        result = runner.invoke(app, ["scan", str(valid_repo), "--workspace", str(workspace_dir)])

    assert result.exit_code == 1
    runs_dir = workspace_dir / "runs"
    run_dirs = list(runs_dir.iterdir())
    data = json.loads((runs_dir / run_dirs[0].name / "run.json").read_text())
    assert data["status"] == "failed"
    assert data["triggered_by"] == "github:octocat"


# ---------------------------------------------------------------------------
# _detect_tool unit tests (issue: scanner must probe `python -m <tool>`
# before falling back to PATH, mirroring the validator's default invocation
# fixed for #223 in agents/validator/runners.py)
# ---------------------------------------------------------------------------

import sys as _sys  # noqa: E402 — local import, unit tests only need this here

from orchestrator.scanners.python import _detect_tool  # noqa: E402


def test_detect_tool_found_via_module_when_not_on_path():
    """D-010 regression: which() misses but the module is importable."""

    def _no_which(cmd: str) -> str | None:
        return None

    def _module_hit(args, **kwargs):
        from unittest.mock import MagicMock

        assert args == [_sys.executable, "-m", "ruff", "--version"]
        result = MagicMock()
        result.returncode = 0
        result.stdout = "ruff 1.2.3\n"
        result.stderr = ""
        return result

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_no_which),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_module_hit),
    ):
        info = _detect_tool("ruff")

    assert info.available is True
    assert info.version == "ruff 1.2.3"


def test_detect_tool_probes_module_form_first():
    """No existing test pinned the literal -m argv sequence; this one does.
    Also asserts a successful module probe never falls through to PATH."""
    from unittest.mock import MagicMock

    calls = []

    def _record(args, **kwargs):
        calls.append(list(args))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "ruff 1.2.3\n"
        result.stderr = ""
        return result

    with (
        patch(
            "orchestrator.scanners.python.shutil.which", return_value="/usr/bin/ruff"
        ) as mock_which,
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_record),
    ):
        _detect_tool("ruff")

    assert calls[0] == [_sys.executable, "-m", "ruff", "--version"]
    mock_which.assert_not_called()


def test_detect_tool_module_probe_empty_output_still_available():
    """rc==0 with empty stdout/stderr must not raise IndexError and must
    still report available=True with version=None."""
    from unittest.mock import MagicMock

    def _empty_output(args, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    with (
        patch("orchestrator.scanners.python.shutil.which", return_value=None),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_empty_output),
    ):
        info = _detect_tool("ruff")

    assert info.available is True
    assert info.version is None


def test_detect_tool_falls_back_to_path_when_module_missing():
    """Documents the architectural limitation: PATH fallback reports the
    tool available even though the validator's default `-m` invocation
    would fail — correct for a cmd_override user, a false positive for a
    default-`-m` user. See docs/context/discoveries.md."""
    from unittest.mock import MagicMock

    def _run(args, **kwargs):
        result = MagicMock()
        if args[1] == "-m":
            result.returncode = 1
            result.stdout = ""
            result.stderr = "No module named ruff"
        else:
            result.returncode = 0
            result.stdout = "ruff 1.2.3\n"
            result.stderr = ""
        return result

    with (
        patch("orchestrator.scanners.python.shutil.which", return_value="/usr/bin/ruff"),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_run),
    ):
        info = _detect_tool("ruff")

    assert info.available is True
    assert info.version == "ruff 1.2.3"


def test_detect_tool_unavailable_when_both_probes_fail():
    from unittest.mock import MagicMock

    def _module_miss(args, **kwargs):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "No module named ruff"
        return result

    with (
        patch("orchestrator.scanners.python.shutil.which", return_value=None),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_module_miss),
    ):
        info = _detect_tool("ruff")

    assert info.available is False


def test_detect_tool_path_probe_nonzero_rc_still_available():
    """AC4 guard: a which-hit whose bare --version exits non-zero is still
    available with version=None — today's PATH-only behaviour, preserved."""
    from unittest.mock import MagicMock

    def _run(args, **kwargs):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = ""
        return result

    with (
        patch("orchestrator.scanners.python.shutil.which", return_value="/usr/bin/ruff"),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_run),
    ):
        info = _detect_tool("ruff")

    assert info.available is True
    assert info.version is None


def test_detect_tool_path_probe_timeout_still_available():
    """AC4 guard, timeout sub-case: a which-hit whose bare --version call
    times out is still available with version=None — this is the PATH
    branch's own timeout handling in _probe_path, distinct from the
    module-probe timeout tests below (which cover _probe_module falling
    through to PATH, not _probe_path's own exception handling)."""

    def _run(args, **kwargs):
        if args[1] == "-m":
            raise subprocess.TimeoutExpired(cmd=args, timeout=10)
        raise subprocess.TimeoutExpired(cmd=args, timeout=10)

    with (
        patch("orchestrator.scanners.python.shutil.which", return_value="/usr/bin/ruff"),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_run),
    ):
        info = _detect_tool("ruff")

    assert info.available is True
    assert info.version is None


def test_detect_tool_module_timeout_falls_through():
    """A timeout on the -m probe does not prove importability — it must be
    treated as a miss and fall through to PATH, not as a hit."""
    from unittest.mock import MagicMock

    def _run(args, **kwargs):
        if args[1] == "-m":
            raise subprocess.TimeoutExpired(cmd=args, timeout=10)
        result = MagicMock()
        result.returncode = 0
        result.stdout = "ruff 1.2.3\n"
        result.stderr = ""
        return result

    with (
        patch("orchestrator.scanners.python.shutil.which", return_value="/usr/bin/ruff"),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_run),
    ):
        info = _detect_tool("ruff")

    assert info.available is True
    assert info.version == "ruff 1.2.3"


def test_detect_tool_module_oserror_falls_through():
    """An OSError on the -m probe (e.g. a broken sys.executable) must also
    be treated as a miss and fall through to PATH."""
    from unittest.mock import MagicMock

    def _run(args, **kwargs):
        if args[1] == "-m":
            raise OSError("broken interpreter")
        result = MagicMock()
        result.returncode = 0
        result.stdout = "ruff 1.2.3\n"
        result.stderr = ""
        return result

    with (
        patch("orchestrator.scanners.python.shutil.which", return_value="/usr/bin/ruff"),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_run),
    ):
        info = _detect_tool("ruff")

    assert info.available is True
    assert info.version == "ruff 1.2.3"


def test_scan_unsupported_reason_wording_no_longer_claims_path_only(valid_repo: Path):
    """AC5: the existing negative tests only assert a case-insensitive
    substring ('ruff'/'pytest' in reason), which would not catch a
    regression back to the old 'not found in PATH' wording. This test
    exercises scan() (where unsupported_reasons is actually built) and
    pins the literal new strings for both tools."""
    from unittest.mock import MagicMock

    def _which_none(cmd: str) -> str | None:
        return None

    def _module_miss_all(args, **kwargs):
        # -m ruff / -m pytest both miss; every other subprocess.run call
        # (git, plus the -m probes falling through to a which()-gated PATH
        # probe that never fires) is handled by _mock_tool_run, which
        # tolerates the shared-subprocess-module git calls (see module
        # docstring above).
        if len(args) > 2 and args[1] == "-m":
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = f"No module named {args[2]}"
            return result
        return _mock_tool_run(args, **kwargs)

    with (
        patch("orchestrator.scanners.python.shutil.which", side_effect=_which_none),
        patch("orchestrator.scanners.python.subprocess.run", side_effect=_module_miss_all),
    ):
        findings = scan(valid_repo)

    assert findings.v1_supported is False
    assert "Ruff not found (tried python -m ruff and PATH)" in findings.unsupported_reasons
    assert "Pytest not found (tried python -m pytest and PATH)" in findings.unsupported_reasons
