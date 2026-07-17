"""Deterministic Python repository scanner for PatchForge V1.

Produces :class:`~orchestrator.schemas.findings.ScanFindings` from a plain
``os.walk`` + ``ast.parse`` pass — no AI, no network, no API keys required.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Optional

from orchestrator.git import current_branch, current_head, resolve_git_root
from orchestrator.schemas.findings import (
    Hotspot,
    PyProjectInfo,
    PythonPackageInfo,
    ScanFindings,
    TestSuiteInfo,
    ToolInfo,
)

# Directories to skip entirely during the file walk.
DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".eggs",
    }
)

# Filenames that indicate a likely application entry point.
_ENTRY_POINTS: frozenset[str] = frozenset({"main.py", "app.py", "cli.py", "__main__.py"})


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _probe_module(cmd: str) -> Optional[ToolInfo]:
    """Probe *cmd* via ``sys.executable -m cmd --version``.

    Mirrors the validator's default invocation (see
    ``agents/validator/runners.py``). Returns ``None`` on any non-success
    outcome (non-zero exit, timeout, or OSError) — none of those prove the
    module is importable, so the caller must fall back to a PATH probe.
    """
    try:
        res = subprocess.run(
            [sys.executable, "-m", cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    raw = (res.stdout or res.stderr).strip()
    version = raw.splitlines()[0] if raw else None
    return ToolInfo(available=True, version=version)


def _probe_path(cmd: str) -> ToolInfo:
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
            timeout=10,
        )
        raw = (res.stdout or res.stderr).strip()
        version = raw.splitlines()[0] if res.returncode == 0 else None
        return ToolInfo(available=True, version=version)
    except (subprocess.TimeoutExpired, OSError):
        return ToolInfo(available=True, version=None)


def _detect_tool(cmd: str) -> ToolInfo:
    """Return availability and version string for *cmd*.

    Probes ``sys.executable -m cmd`` first so detection predicts what the
    validator's default invocation will actually do (issue #223 fixed this
    for the validator; this mirrors it for scan-time detection). Falls back
    to a PATH-based probe (``shutil.which`` + bare invocation) to cover a
    ``cmd_override`` that relies on a PATH-installed binary instead.
    """
    result = _probe_module(cmd)
    if result is not None:
        return result
    return _probe_path(cmd)


def _check_pyproject(target: Path) -> tuple[PyProjectInfo, dict | None]:
    """Inspect pyproject.toml in *target* and return (info, parsed_dict|None).

    Returns:
        A tuple of :class:`PyProjectInfo` and the raw parsed dict (or ``None``
        when the file is missing or invalid).
    """
    path = target / "pyproject.toml"
    if not path.exists():
        return PyProjectInfo(exists=False, valid=False), None
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return PyProjectInfo(exists=True, valid=False, error=str(exc)), None
    backend = data.get("build-system", {}).get("build-backend")
    return PyProjectInfo(exists=True, valid=True, build_backend=backend), data


def _count_definitions(source: str) -> int:
    """Count ``FunctionDef`` + ``AsyncFunctionDef`` + ``ClassDef`` nodes in *source*.

    Returns 0 if ``ast.parse`` raises :class:`SyntaxError`.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            count += 1
    return count


def _detect_test_suite_info(target: Path, pyproject_data: Optional[dict]) -> TestSuiteInfo:
    """Detect test suite presence via filesystem conventions and pyproject.toml config.

    Args:
        target: Root path of the repository to inspect.
        pyproject_data: Optional parsed pyproject.toml dict for detecting
            pytest ini-options configuration.

    Returns:
        A :class:`TestSuiteInfo` with ``detected`` set to True if a test
        directory, config file, or matching glob pattern is found.
    """
    if (target / "tests").is_dir():
        return TestSuiteInfo(detected=True, type="tests_dir")
    if (target / "test").is_dir():
        return TestSuiteInfo(detected=True, type="test_dir")
    if (target / "pytest.ini").exists():
        return TestSuiteInfo(detected=True, type="pytest_ini")
    if (target / "conftest.py").exists():
        return TestSuiteInfo(detected=True, type="conftest")
    if pyproject_data is not None:
        ini_opts = pyproject_data.get("tool", {}).get("pytest", {}).get("ini_options", {})
        if ini_opts:
            return TestSuiteInfo(detected=True, type="pyproject_config")
    for pattern in ("test_*.py", "*_test.py"):
        if list(target.glob(pattern)):
            return TestSuiteInfo(detected=True, type="test_glob")
    return TestSuiteInfo(detected=False)


def _has_typescript(target: Path, ignore_dirs: frozenset[str]) -> bool:
    """Return True if any .ts or .tsx files exist under *target*."""
    for _root_str, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for f in files:
            if f.endswith((".ts", ".tsx")):
                return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan(
    target_path: Path,
    ignore_dirs: Optional[list[str]] = None,
) -> ScanFindings:
    """Run a deterministic scan of the Python repository at *target_path*.

    Args:
        target_path: Absolute path to the root of the repository to scan.
        ignore_dirs: Optional list of directory *names* to skip during the
            walk.  Merged with :data:`DEFAULT_IGNORE_DIRS`.

    Returns:
        A fully populated :class:`~orchestrator.schemas.findings.ScanFindings`
        instance.  Does **not** write any files — callers are responsible for
        persistence.
    """
    target_path = Path(target_path).resolve()

    _ignore: frozenset[str] = DEFAULT_IGNORE_DIRS
    if ignore_dirs:
        _ignore = _ignore | frozenset(ignore_dirs)

    # --- Git metadata ---------------------------------------------------
    git_root = resolve_git_root(target_path)
    try:
        base_commit = current_head(git_root)
    except RuntimeError:
        base_commit = ""  # empty repo with no commits
    try:
        branch = current_branch(git_root)
    except RuntimeError:
        branch = ""  # empty repo with no commits

    # --- Static analysis of pyproject.toml ------------------------------
    pyproject, _pyproject_data = _check_pyproject(target_path)

    # --- Tool detection -------------------------------------------------
    ruff_info = _detect_tool("ruff")
    pytest_info = _detect_tool("pytest")

    # --- Test-suite detection -------------------------------------------
    test_suite = _detect_test_suite_info(target_path, _pyproject_data)

    # --- File inventory -------------------------------------------------
    python_files: list[Path] = []
    package_paths: list[Path] = []

    for root_str, dirs, files in os.walk(target_path):
        dirs[:] = [d for d in dirs if d not in _ignore]
        root = Path(root_str)

        if "__init__.py" in files:
            package_paths.append(root)

        for f in files:
            if f.endswith(".py"):
                python_files.append(root / f)

    packages = [
        PythonPackageInfo(
            path=str(p.relative_to(target_path)),
            is_package=True,
        )
        for p in sorted(package_paths)
    ]
    modules = sorted(str(f.relative_to(target_path)).replace(os.sep, "/") for f in python_files)

    # --- Hotspot computation --------------------------------------------
    hotspots: list[Hotspot] = []

    # Build per-file metrics in one pass to avoid reading each file twice.
    file_metrics: list[tuple[Path, int, int]] = []  # (path, lines, defs)
    for py_file in python_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            source = ""
        lines = len(source.splitlines())
        defs = _count_definitions(source)
        file_metrics.append((py_file, lines, defs))

    # Top-5 largest files
    for py_file, lines, _ in sorted(file_metrics, key=lambda t: t[1], reverse=True)[:5]:
        hotspots.append(
            Hotspot(
                file=str(py_file.relative_to(target_path)).replace(os.sep, "/"),
                reason="largest_file",
                metric="lines",
                value=float(lines),
            )
        )

    # Top-5 files by function/class count
    for py_file, _, defs in sorted(file_metrics, key=lambda t: t[2], reverse=True)[:5]:
        if defs > 0:
            hotspots.append(
                Hotspot(
                    file=str(py_file.relative_to(target_path)).replace(os.sep, "/"),
                    reason="most_definitions",
                    metric="definitions",
                    value=float(defs),
                )
            )

    # Likely entry points
    for py_file, _, _ in file_metrics:
        if py_file.name in _ENTRY_POINTS:
            hotspots.append(
                Hotspot(
                    file=str(py_file.relative_to(target_path)).replace(os.sep, "/"),
                    reason="entry_point",
                    metric="name",
                )
            )

    # Test files
    for py_file, _, _ in file_metrics:
        name = py_file.name
        if name.startswith("test_") or name.endswith("_test.py"):
            hotspots.append(
                Hotspot(
                    file=str(py_file.relative_to(target_path)).replace(os.sep, "/"),
                    reason="test_file",
                    metric="name",
                )
            )

    # Package structure
    for pkg in packages:
        hotspots.append(
            Hotspot(
                file=pkg.path,
                reason="package",
                metric="is_package",
                value=1.0,
            )
        )

    # --- V1 support determination ---------------------------------------
    support_reasons: list[str] = []
    unsupported_reasons: list[str] = []

    if pyproject.exists and pyproject.valid:
        support_reasons.append("pyproject.toml exists and is valid")
    else:
        if not pyproject.exists:
            unsupported_reasons.append("pyproject.toml not found")
        else:
            unsupported_reasons.append(f"pyproject.toml is invalid TOML: {pyproject.error}")

    if ruff_info.available:
        support_reasons.append(f"Ruff available: {ruff_info.version}")
    else:
        unsupported_reasons.append("Ruff not found (tried python -m ruff and PATH)")

    if pytest_info.available:
        support_reasons.append(f"Pytest available: {pytest_info.version}")
    else:
        unsupported_reasons.append("Pytest not found (tried python -m pytest and PATH)")

    if test_suite.detected:
        support_reasons.append(f"Test suite detected ({test_suite.type})")
    else:
        unsupported_reasons.append("No test suite detected")

    # TypeScript: WARN only, does not affect v1_supported
    if _has_typescript(target_path, _ignore):
        support_reasons.append("TypeScript files detected (Python-only V1 will ignore them)")

    v1_supported = len(unsupported_reasons) == 0

    return ScanFindings(
        repository_root=str(git_root),
        base_commit=base_commit,
        branch=branch,
        v1_supported=v1_supported,
        support_reasons=support_reasons,
        unsupported_reasons=unsupported_reasons,
        pyproject=pyproject,
        ruff=ruff_info,
        pytest=pytest_info,
        test_suite=test_suite,
        total_python_files=len(python_files),
        packages=packages,
        modules=modules,
        hotspots=hotspots,
    )
