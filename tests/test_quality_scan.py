"""Tests for the deterministic quality scanner.

Covers all 11 checks across 4 dimensions:
readability, complexity, safety, hygiene.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.scanners.quality import scan
from orchestrator.schemas.quality import QualityCheck, QualityDimension, QualityReport

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_clean_package(repo)
    return repo


@pytest.fixture()
def messy_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_messy_package(repo)
    return repo


@pytest.fixture()
def empty_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(repo: Path, rel_path: str, content: str) -> Path:
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return full


def _make_clean_package(repo: Path) -> None:
    _write(
        repo,
        "app.py",
        '''"""Application module."""

from __future__ import annotations


def greet(name: str) -> str:
    """Return a greeting."""
    return f"Hello, {name}"


class Calculator:
    """A simple calculator."""

    def add(self, a: int, b: int) -> int:
        """Add two numbers."""
        return a + b
''',
    )


def _make_messy_package(repo: Path) -> None:
    _write(
        repo,
        "messy.py",
        """import sys
from os import *

def f(a, b):
    print(a)
    if True:
        if True:
            if True:
                if True:
                    if True:
                        pass
    try:
        pass
    except:
        pass
    exec("x = 1")
    assert a > 0
    # TODO: fix this
""",
    )
    _write(
        repo,
        "utils.py",
        '''"""Utility functions."""

from __future__ import annotations


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b
''',
    )
    _write(repo, "__main__.py", '"""Entry point."""\n\nprint("hello")\n')
    _write(repo, "tests/test_ok.py", '"""Test module."""\n\ndef test_ok():\n    assert True\n')


# ---------------------------------------------------------------------------
# Shape tests
# ---------------------------------------------------------------------------


class TestScanShape:
    def test_returns_quality_report(self, clean_repo: Path) -> None:
        result = scan(clean_repo)
        assert isinstance(result, QualityReport)

    def test_has_all_dimensions(self, clean_repo: Path) -> None:
        result = scan(clean_repo)
        assert set(result.dimensions.keys()) == {
            "readability",
            "complexity",
            "safety",
            "hygiene",
        }

    def test_each_dimension_has_checks(self, clean_repo: Path) -> None:
        result = scan(clean_repo)
        for name, dim in result.dimensions.items():
            assert isinstance(dim, QualityDimension)
            assert dim.name == name
            assert len(dim.checks) >= 1
            for check in dim.checks:
                assert isinstance(check, QualityCheck)

    def test_overall_score_is_int(self, clean_repo: Path) -> None:
        result = scan(clean_repo)
        assert isinstance(result.overall_score, int)
        assert 0 <= result.overall_score <= 100

    def test_empty_repo_returns_perfect_scores(self, empty_repo: Path) -> None:
        result = scan(empty_repo)
        assert result.overall_score == 100
        for dim in result.dimensions.values():
            assert dim.score == 100


# ---------------------------------------------------------------------------
# Readability checks
# ---------------------------------------------------------------------------


class TestReadability:
    def test_clean_repo_all_checks_pass(self, clean_repo: Path) -> None:
        result = scan(clean_repo)
        rd = result.dimensions["readability"]
        for check in rd.checks:
            assert check.passed, f"{check.id} should pass: {check.message}"
            assert check.score == 100

    def test_missing_docstrings_detected(self, messy_repo: Path) -> None:
        result = scan(messy_repo)
        rd = result.dimensions["readability"]
        docs = next(c for c in rd.checks if c.id == "missing-docstrings")
        assert not docs.passed

    def test_missing_annotations_detected(self, messy_repo: Path) -> None:
        result = scan(messy_repo)
        rd = result.dimensions["readability"]
        ann = next(c for c in rd.checks if c.id == "missing-annotations")
        assert not ann.passed

    def test_long_functions_detected(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        lines = "\n".join(f"    x = {i}" for i in range(50))
        _write(
            repo,
            "long.py",
            f"""def long_func():\n{lines}\n""",
        )
        result = scan(repo)
        rd = result.dimensions["readability"]
        lf = next(c for c in rd.checks if c.id == "long-functions")
        assert not lf.passed


# ---------------------------------------------------------------------------
# Complexity checks
# ---------------------------------------------------------------------------


class TestComplexity:
    def test_clean_repo_passes(self, clean_repo: Path) -> None:
        result = scan(clean_repo)
        cx = result.dimensions["complexity"]
        for check in cx.checks:
            assert check.passed, f"{check.id} should pass: {check.message}"

    def test_deep_nesting_detected(self, messy_repo: Path) -> None:
        result = scan(messy_repo)
        cx = result.dimensions["complexity"]
        nesting = next(c for c in cx.checks if c.id == "deep-nesting")
        assert not nesting.passed


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------


class TestSafety:
    def test_clean_repo_passes(self, clean_repo: Path) -> None:
        result = scan(clean_repo)
        sf = result.dimensions["safety"]
        for check in sf.checks:
            assert check.passed, f"{check.id} should pass: {check.message}"

    def test_bare_except_detected(self, messy_repo: Path) -> None:
        result = scan(messy_repo)
        sf = result.dimensions["safety"]
        be = next(c for c in sf.checks if c.id == "bare-except")
        assert not be.passed

    def test_dangerous_apis_detected(self, messy_repo: Path) -> None:
        result = scan(messy_repo)
        sf = result.dimensions["safety"]
        da = next(c for c in sf.checks if c.id == "dangerous-apis")
        assert not da.passed

    def test_assert_in_nontest_detected(self, messy_repo: Path) -> None:
        result = scan(messy_repo)
        sf = result.dimensions["safety"]
        ait = next(c for c in sf.checks if c.id == "assert-in-nontest")
        assert not ait.passed


# ---------------------------------------------------------------------------
# Hygiene checks
# ---------------------------------------------------------------------------


class TestHygiene:
    def test_clean_repo_passes(self, clean_repo: Path) -> None:
        result = scan(clean_repo)
        hy = result.dimensions["hygiene"]
        for check in hy.checks:
            assert check.passed, f"{check.id} should pass: {check.message}"

    def test_large_files_detected(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        lines = "\n".join(f"# line {i}" for i in range(600))
        _write(repo, "big.py", lines)
        result = scan(repo)
        hy = result.dimensions["hygiene"]
        lf = next(c for c in hy.checks if c.id == "large-files")
        assert not lf.passed

    def test_todos_detected(self, messy_repo: Path) -> None:
        result = scan(messy_repo)
        hy = result.dimensions["hygiene"]
        td = next(c for c in hy.checks if c.id == "todos")
        assert not td.passed

    def test_stray_prints_detected(self, messy_repo: Path) -> None:
        result = scan(messy_repo)
        hy = result.dimensions["hygiene"]
        sp = next(c for c in hy.checks if c.id == "stray-prints")
        assert not sp.passed

    def test_wildcard_imports_detected(self, messy_repo: Path) -> None:
        result = scan(messy_repo)
        hy = result.dimensions["hygiene"]
        wi = next(c for c in hy.checks if c.id == "wildcard-imports")
        assert not wi.passed

    def test_main_guard_prints_are_clean(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(
            repo,
            "ok.py",
            '''"""Module with guarded print."""

def work() -> None:
    pass

if __name__ == "__main__":
    print("running")
''',
        )
        result = scan(repo)
        hy = result.dimensions["hygiene"]
        sp = next(c for c in hy.checks if c.id == "stray-prints")
        assert sp.passed, f"{sp.message}"

    def test_test_dir_prints_ignored(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, "tests/test_foo.py", '"""Test."""\n\nprint("debug")\n')
        result = scan(repo)
        hy = result.dimensions["hygiene"]
        sp = next(c for c in hy.checks if c.id == "stray-prints")
        assert sp.passed
