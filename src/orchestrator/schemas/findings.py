"""V1 deterministic scan findings schema.

These models describe the output of ``orchestrator.scanners.python.scan``.
They are intentionally separate from :class:`~orchestrator.schemas.scout_output.ScoutOutput`
(the AI Scout schema) so that :mod:`orchestrator.commands.plan` can detect which
format is present in ``findings.json`` and emit a clear error when V1 findings
are found where AI-based Scout output is expected.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PyProjectInfo(BaseModel):
    """Status of pyproject.toml in the target repository."""

    exists: bool
    valid: bool
    build_backend: Optional[str] = None
    error: Optional[str] = None


class ToolInfo(BaseModel):
    """Availability and version of an external CLI tool."""

    available: bool
    version: Optional[str] = None


class TestSuiteInfo(BaseModel):
    """Whether a test suite is detectable and how it was found."""

    __test__ = False  # Prevent pytest from collecting this as a test class.

    detected: bool
    type: Optional[str] = None
    # Possible values: tests_dir, test_dir, pytest_ini, conftest,
    # pyproject_config, test_glob


class PythonPackageInfo(BaseModel):
    """A directory that is a Python package (contains __init__.py)."""

    path: str
    is_package: bool


class Hotspot(BaseModel):
    """A file of interest identified by the deterministic scanner."""

    file: str
    reason: str
    metric: str = ""
    value: Optional[float] = None


class ScanFindings(BaseModel):
    """Root model for V1 deterministic scan findings written to ``findings.json``."""

    repository_root: str
    base_commit: str
    branch: str
    v1_supported: bool
    support_reasons: list[str]
    unsupported_reasons: list[str]
    pyproject: PyProjectInfo
    ruff: ToolInfo
    pytest: ToolInfo
    test_suite: TestSuiteInfo
    total_python_files: int
    packages: list[PythonPackageInfo]
    modules: list[str]
    hotspots: list[Hotspot]
