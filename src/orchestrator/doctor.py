from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Optional

from orchestrator.schemas.doctor import CheckResult, CheckStatus, DoctorResult


def check_command_available(cmd: str) -> tuple[bool, str]:
    """Return (available, version_str) for *cmd*.

    Returns (False, '') when the command is not found or cannot be executed.
    Returns (False, 'timed out') when the command exceeds the 30-second timeout.
    """
    try:
        res = subprocess.run(
            [cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode == 0:
            return True, res.stdout.strip()
        return False, res.stderr.strip()
    except (FileNotFoundError, OSError):
        return False, ""
    except subprocess.TimeoutExpired:
        return False, "timed out"


def detect_test_suite(path: Path, pyproject: Optional[dict] = None) -> bool:
    """Return True if a test suite is detectable at *path*.

    Detection criteria (first match wins): tests/ directory, test/ directory,
    pytest.ini, conftest.py, [tool.pytest.ini_options] in pyproject.toml,
    or any file matching test_*.py or *_test.py.
    """
    if (path / "tests").is_dir():
        return True
    if (path / "test").is_dir():
        return True
    if (path / "pytest.ini").exists():
        return True
    if (path / "conftest.py").exists():
        return True
    if pyproject is not None:
        ini_opts = pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})
        if ini_opts:
            return True

    for pattern in ("test_*.py", "*_test.py"):
        matched = list(path.glob(pattern))
        if matched:
            return True

    return False


def _detect_typescript(path: Path) -> bool:
    """Return True if any .ts or .tsx files exist under *path*."""
    return any(any(path.rglob(pattern)) for pattern in ("*.ts", "*.tsx"))


def _read_orchestrator_config(path: Path) -> dict:
    """Return orchestrator.json contents as a dict, or {} if unavailable.

    Returns {} when the file is missing, contains invalid JSON,
    or the root value is not a JSON object.
    """
    config_file = path / "orchestrator.json"
    if not config_file.exists():
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def check_git(path: Path) -> tuple[CheckResult, Optional[str], Optional[str], Optional[bool]]:
    """Validate Git repository state for *path*.

    Returns a 4-tuple (result, branch, head, is_dirty):
    - result: CheckResult with PASS/FAIL for the git_repository check
    - branch: current branch name, or "unknown" if not determinable
    - head: current commit hash (40 hex characters), or "unknown" if not available
    - is_dirty: True when the working tree has uncommitted changes
    """
    from orchestrator.git import (
        current_branch,
        current_head,
        is_git_repo,
        is_working_tree_clean,
        resolve_git_root,
    )

    if not is_git_repo(path):
        return (
            CheckResult(
                name="git_repository",
                status=CheckStatus.FAIL,
                message="Not a Git repository",
                detail=f"Target path is not inside a Git repository: {path}",
                fix_hint=(
                    "Run 'git init' to initialize a repository, or point to a Git-tracked directory"
                ),
            ),
            None,
            None,
            None,
        )

    root = resolve_git_root(path)
    branch = current_branch(root)
    head = current_head(root)
    is_clean = is_working_tree_clean(root)

    return (
        CheckResult(
            name="git_repository",
            status=CheckStatus.PASS,
            message=f"Git repository detected on branch '{branch}'",
            detail=f"Root: {root}\nHEAD: {head}",
        ),
        branch or "unknown",
        head or "unknown",
        not is_clean,
    )


def check_workspace(path: Path) -> tuple[CheckResult, Optional[str]]:
    """Validate the workspace path is outside the target repository.

    Looks up workspace_path in orchestrator.json first.
    Falls back to the default workspace path when the config key is not set.
    """
    from orchestrator.schemas.config import default_workspace_path, validate_workspace_path

    cfg = _read_orchestrator_config(path)
    raw_ws = cfg.get("workspace_path")

    if raw_ws is not None:
        try:
            explicit_ws = Path(raw_ws).expanduser().resolve()
            valid_ws = validate_workspace_path(path, explicit_ws)
            ws_str = str(valid_ws.resolve())
            return (
                CheckResult(
                    name="workspace",
                    status=CheckStatus.PASS,
                    message="Workspace path is outside the target repository",
                    detail=f"Workspace (from orchestrator.json): {ws_str}",
                ),
                ws_str,
            )
        except ValueError as exc:
            return (
                CheckResult(
                    name="workspace",
                    status=CheckStatus.FAIL,
                    message="Workspace path is inside the target repository",
                    detail=str(exc),
                    fix_hint=(
                        "Set workspace_path in orchestrator.json "
                        "to a directory outside the repository"
                    ),
                ),
                None,
            )

    default_ws = default_workspace_path(path)
    try:
        valid_ws = validate_workspace_path(path, default_ws)
        ws_str = str(valid_ws.resolve())
        return (
            CheckResult(
                name="workspace",
                status=CheckStatus.PASS,
                message="Workspace path is outside the target repository",
                detail=f"Workspace: {ws_str}",
            ),
            ws_str,
        )
    except ValueError as exc:
        return (
            CheckResult(
                name="workspace",
                status=CheckStatus.FAIL,
                message="Workspace path is inside the target repository",
                detail=str(exc),
                fix_hint=(
                    "Set workspace_path in orchestrator.json to a directory outside the repository"
                ),
            ),
            None,
        )


def check_pyproject(path: Path) -> tuple[CheckResult, Optional[dict]]:
    """Check pyproject.toml exists, is valid TOML, and has a build system."""
    pyproject_path = path / "pyproject.toml"

    if not pyproject_path.exists():
        return (
            CheckResult(
                name="pyproject_toml",
                status=CheckStatus.FAIL,
                message="pyproject.toml not found",
                detail=f"Expected at: {pyproject_path}",
                fix_hint="Create a pyproject.toml file to define your Python project",
            ),
            None,
        )

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        return (
            CheckResult(
                name="pyproject_toml",
                status=CheckStatus.FAIL,
                message="pyproject.toml is not valid TOML",
                detail=str(exc),
                fix_hint="Fix the syntax error in pyproject.toml",
            ),
            None,
        )

    build_system = data.get("build-system")
    if not isinstance(build_system, dict):
        return (
            CheckResult(
                name="pyproject_toml",
                status=CheckStatus.FAIL,
                message="pyproject.toml is missing [build-system]",
                detail="A Python project must declare a build system in pyproject.toml",
                fix_hint=(
                    "Add a [build-system] section, e.g.:\n"
                    '[build-system]\nrequires = ["hatchling"]\n'
                    'build-backend = "hatchling.build"'
                ),
            ),
            data,
        )

    return (
        CheckResult(
            name="pyproject_toml",
            status=CheckStatus.PASS,
            message="pyproject.toml exists and is valid",
            detail=f"Build system: {build_system.get('build-backend', 'unknown')}",
        ),
        data,
    )


def check_ruff(path: Path) -> CheckResult:
    """Check Ruff is available or explicitly configured."""
    config = _read_orchestrator_config(path)
    lint_command = config.get("lint_command")

    if lint_command is not None and isinstance(lint_command, list) and len(lint_command) > 0:
        return CheckResult(
            name="ruff",
            status=CheckStatus.PASS,
            message="Lint command is explicitly configured in orchestrator.json",
            detail=f"lint_command: {lint_command}",
        )

    found, version_str = check_command_available("ruff")
    if found:
        return CheckResult(
            name="ruff",
            status=CheckStatus.PASS,
            message=f"Ruff is available: {version_str}",
        )

    return CheckResult(
        name="ruff",
        status=CheckStatus.FAIL,
        message="Ruff is not available",
        detail="'ruff --version' did not return successfully",
        fix_hint=("Install ruff with: pip install ruff, or set lint_command in orchestrator.json"),
    )


def check_pytest(path: Path, pyproject: Optional[dict] = None) -> CheckResult:
    """Check Pytest is available and a test suite is detectable."""
    config = _read_orchestrator_config(path)
    test_command = config.get("test_command")

    if test_command is not None and isinstance(test_command, list) and len(test_command) > 0:
        if detect_test_suite(path, pyproject):
            return CheckResult(
                name="pytest",
                status=CheckStatus.PASS,
                message=(
                    "Test command is explicitly configured "
                    "in orchestrator.json, and a test suite is detectable"
                ),
                detail=f"test_command: {test_command}",
            )
        return CheckResult(
            name="pytest",
            status=CheckStatus.FAIL,
            message="Test command is configured but no test suite is detectable",
            detail=(
                "No tests/, test/, pytest.ini, conftest.py, "
                "[tool.pytest.ini_options], or test_*/*_test.py files found"
            ),
            fix_hint="Create a tests/ directory or a conftest.py to define your test suite",
        )

    found, version_str = check_command_available("pytest")
    if not found:
        return CheckResult(
            name="pytest",
            status=CheckStatus.FAIL,
            message="Pytest is not available and no test_command is configured",
            detail="'pytest --version' did not return successfully",
            fix_hint=(
                "Install pytest with: pip install pytest, or set test_command in orchestrator.json"
            ),
        )

    if not detect_test_suite(path, pyproject):
        return CheckResult(
            name="pytest",
            status=CheckStatus.FAIL,
            message="Pytest is available but no test suite is detectable",
            detail=(
                f"Pytest version: {version_str}. No tests/, test/, "
                "pytest.ini, conftest.py, [tool.pytest.ini_options], "
                "or test_*/*_test.py files found"
            ),
            fix_hint="Create a tests/ directory or a conftest.py to define your test suite",
        )

    return CheckResult(
        name="pytest",
        status=CheckStatus.PASS,
        message=f"Pytest is available and a test suite is detectable: {version_str}",
    )


def check_api_keys() -> list[CheckResult]:
    """Check which API keys are set in the environment; return warnings.

    Checks three environment variables: ANTHROPIC_API_KEY, GOOGLE_API_KEY,
    and OPENROUTER_API_KEY. Returns one WARN-level CheckResult per missing key.
    These checks are not required for V1 support.
    """
    keys = [
        ("anthropic_api_key", "ANTHROPIC_API_KEY", "Claude"),
        ("google_api_key", "GOOGLE_API_KEY", "Gemini"),
        ("openrouter_api_key", "OPENROUTER_API_KEY", "OpenRouter"),
    ]
    results = []
    for name, env_var, provider in keys:
        if not os.environ.get(env_var):
            results.append(
                CheckResult(
                    name=name,
                    status=CheckStatus.WARN,
                    message=f"{env_var} not configured ({provider})",
                    fix_hint=f"Set {env_var} in your environment before running scan",
                    required=False,
                )
            )
    return results


def check(path: str | Path) -> DoctorResult:
    """Run all doctor checks on *path* and return a DoctorResult.

    Sub-checks executed: git_repository, workspace, pyproject_toml, ruff, pytest, api_keys,
    and typescript. A working_tree WARN check is added only when the repo is dirty.
    V1 support is determined by whether all required checks pass.
    """
    target = Path(path).resolve()
    checks: list[CheckResult] = []
    workspace_path: Optional[str] = None
    git_branch: Optional[str] = None
    git_head: Optional[str] = None
    is_dirty: Optional[bool] = None

    git_result, branch, head, dirty = check_git(target)
    checks.append(git_result)
    git_branch = branch
    git_head = head
    is_dirty = dirty

    if is_dirty:
        checks.append(
            CheckResult(
                name="working_tree",
                status=CheckStatus.WARN,
                message="Working tree has uncommitted changes",
                detail=(
                    "The apply command will block on a dirty working tree "
                    "unless --allow-dirty is used"
                ),
                required=False,
            )
        )

    ws_result, ws_path = check_workspace(target)
    checks.append(ws_result)
    if ws_path is not None:
        workspace_path = ws_path

    pyproject_result, pyproject_data = check_pyproject(target)
    checks.append(pyproject_result)

    checks.append(check_ruff(target))
    checks.append(check_pytest(target, pyproject_data))
    checks.extend(check_api_keys())

    if _detect_typescript(target):
        checks.append(
            CheckResult(
                name="typescript",
                status=CheckStatus.WARN,
                message="TypeScript files detected — V1 only supports Python",
                fix_hint="TypeScript support is planned for a future phase",
                required=False,
            )
        )

    v1_supported = all(check.status != CheckStatus.FAIL for check in checks if check.required)

    return DoctorResult(
        target_path=str(target),
        v1_supported=v1_supported,
        checks=checks,
        workspace_path=workspace_path,
        git_branch=git_branch,
        git_head=git_head,
        is_dirty=is_dirty if is_dirty is not None else False,
    )
