"""Deterministic repository quality scanner for PatchForge V1.

Produces :class:`~orchestrator.schemas.quality.QualityReport` from a plain
``os.walk`` + ``ast.parse`` pass — no AI, no network, no API keys required.

Note:
    This scanner is designed to run against the root of a project.
    Scanning isolated subdirectories may produce inaccurate results
    for checks that depend on project-layout heuristics
    (test detection, script detection, etc.).
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Optional

from orchestrator.schemas.quality import QualityCheck, QualityDimension, QualityReport

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

MAX_FUNCTION_LINES: int = 40
MAX_FILE_LINES: int = 500
MAX_NESTING_DEPTH: int = 4
MAX_FUNCTION_STATEMENTS: int = 30

# Excluded from stray-prints and related checks.
_EXCLUDED_DIRS: frozenset[str] = frozenset(
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

_SKIP_DIRS_CHECK: frozenset[str] = frozenset({"tests", "scripts", "examples"})

_DUNDER_METHODS: frozenset[str] = frozenset(
    {
        "__init__",
        "__str__",
        "__repr__",
        "__eq__",
        "__hash__",
        "__call__",
        "__enter__",
        "__exit__",
        "__len__",
        "__getitem__",
        "__setitem__",
        "__contains__",
        "__iter__",
        "__next__",
        "__aenter__",
        "__aexit__",
        "__aiter__",
        "__anext__",
    }
)

_TODO_PATTERN: re.Pattern = re.compile(r"\b(?:TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _has_docstring(body: list[ast.stmt]) -> bool:
    """Return True if *body* starts with a string constant docstring."""
    return (
        bool(body)
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )


def _function_length(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Return physical line count of a function body, excluding its docstring."""
    total = func_node.end_lineno - (func_node.lineno or 0)
    body = func_node.body
    if _has_docstring(body):
        doc = body[0]
        doc_lines = (doc.end_lineno or doc.lineno) - doc.lineno + 1
        total -= doc_lines
    return total


def _is_public(name: str) -> bool:
    """Return True if *name* is public (not prefixed with ``_``)."""
    return not name.startswith("_")


def _is_test_file(file_rel: str) -> bool:
    """Return True if the relative path indicates a test file."""
    parts = file_rel.replace("\\", "/").split("/")
    return (
        any(p == "tests" or p == "test" for p in parts)
        or file_rel.startswith("test_")
        or "_test.py" in file_rel
    )


def _is_excluded_dir(file_rel: str) -> bool:
    """Return True if the relative path starts with an excluded directory."""
    parts = file_rel.replace("\\", "/").split("/")
    return any(p in _SKIP_DIRS_CHECK for p in parts)


def _has_full_annotations(func: ast.FunctionDef) -> bool:
    """Return True if *func* has type annotations for all params and return."""
    if func.returns is None:
        return False
    for arg in func.args.args + func.args.posonlyargs + func.args.kwonlyargs:
        if arg.arg in ("self", "cls"):
            continue
        if arg.annotation is None:
            return False
    if func.args.vararg is not None and func.args.vararg.annotation is None:
        return False
    if func.args.kwarg is not None and func.args.kwarg.annotation is None:
        return False
    return True


def _max_nesting_depth(body: list[ast.stmt], start_depth: int = 1) -> int:
    """Return the maximum control-flow nesting depth in *body*."""
    max_depth = 0

    def walk(nodes: list[ast.stmt], depth: int) -> None:
        nonlocal max_depth
        for node in nodes:
            if isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.Match)):
                max_depth = max(max_depth, depth)
                if isinstance(node, ast.If):
                    walk(node.body, depth + 1)
                    walk(node.orelse, depth + 1)
                elif isinstance(node, ast.For):
                    walk(node.body, depth + 1)
                    walk(node.orelse, depth + 1)
                elif isinstance(node, ast.While):
                    walk(node.body, depth + 1)
                    walk(node.orelse, depth + 1)
                elif isinstance(node, ast.Try):
                    walk(node.body, depth + 1)
                    for handler in node.handlers:
                        walk(handler.body, depth + 1)
                    walk(node.orelse, depth + 1)
                    walk(node.finalbody, depth + 1)
                elif isinstance(node, ast.With):
                    walk(node.body, depth + 1)
                elif isinstance(node, ast.Match):
                    for case in node.cases:
                        walk(case.body, depth + 1)

    walk(body, start_depth)
    return max_depth


def _is_overload_decorator(func: ast.FunctionDef) -> bool:
    """Return True if *func* is decorated with ``@overload``."""
    for dec in func.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "overload":
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == "overload":
            return True
    return False


def _is_in_main_guard(node: ast.AST, tree: ast.Module) -> bool:
    """Return True if *node* is inside an ``if __name__ == '__main__'`` guard."""
    for child in tree.body:
        if isinstance(child, ast.If):
            if _is_name_eq_main(child):
                if _node_in_body(node, child.body):
                    return True
    return False


def _is_name_eq_main(if_node: ast.If) -> bool:
    """Return True if *if_node* tests ``__name__ == '__main__'``."""
    test = if_node.test
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
        left = test.left
        comparators = test.comparators
        if (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and len(comparators) == 1
            and isinstance(comparators[0], ast.Constant)
            and comparators[0].value == "__main__"
        ):
            return True
    return False


def _node_in_body(node: ast.AST, body: list[ast.stmt]) -> bool:
    """Return True if *node* is within *body* (by line number range)."""
    n_lineno = getattr(node, "lineno", None)
    if n_lineno is None:
        return False
    for stmt in body:
        s_lineno = getattr(stmt, "lineno", None)
        s_end = getattr(stmt, "end_lineno", None)
        if s_lineno is not None and s_end is not None and s_lineno <= n_lineno <= s_end:
            return True
    return False


def _count_source_lines(filepath: Path) -> int:
    """Return total source lines (including blanks) in *filepath*."""
    try:
        return len(filepath.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def _collect_python_files(target: Path, ignore_dirs: frozenset[str]) -> list[tuple[Path, str]]:
    """Walk *target* and return sorted list of (absolute_path, relative_path)."""
    result: list[tuple[Path, str]] = []
    for root_str, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        dirs.sort()
        files.sort()
        root = Path(root_str)
        for fname in files:
            if fname.endswith(".py"):
                full = root / fname
                rel = str(full.relative_to(target)).replace(os.sep, "/")
                result.append((full, rel))
    return result


def _score_from_ratio(clean: int, total: int, multiplier: float = 1.0) -> int:
    """Compute a 0-100 score from a clean/total ratio with multiplicative penalty."""
    if total == 0:
        return 100
    violations = total - clean
    ratio = violations / total
    return int(max(0.0, 100.0 - ratio * 100.0 * multiplier))


# ---------------------------------------------------------------------------
# Per-dimension check runners
# ---------------------------------------------------------------------------


def _check_readability(files: list[tuple[Path, str]]) -> list[QualityCheck]:
    clean_docs = 0
    total_docs = 0
    clean_annotations = 0
    total_annotations = 0
    clean_length = 0
    total_length = 0

    for filepath, rel in files:
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        # Module-level docstring.
        if not _is_test_file(rel):
            total_docs += 1
            if _has_docstring(tree.body):
                clean_docs += 1

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if _is_public(node.name) and not _is_test_file(rel):
                    total_docs += 1
                    if _has_docstring(node.body):
                        clean_docs += 1
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if (
                            _is_public(item.name)
                            and item.name not in _DUNDER_METHODS
                            and not _is_test_file(rel)
                        ):
                            total_docs += 1
                            if _has_docstring(item.body):
                                clean_docs += 1
                            if not _is_overload_decorator(item):
                                total_annotations += 1
                                if _has_full_annotations(item):
                                    clean_annotations += 1
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _is_public(node.name) and not _is_test_file(rel):
                    total_docs += 1
                    if _has_docstring(node.body):
                        clean_docs += 1
                    if not _is_overload_decorator(node):
                        total_annotations += 1
                        if _has_full_annotations(node):
                            clean_annotations += 1

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total_length += 1
                if _function_length(node) <= MAX_FUNCTION_LINES:
                    clean_length += 1

    checks = [
        QualityCheck(
            id="missing-docstrings",
            passed=clean_docs == total_docs,
            score=_score_from_ratio(clean_docs, total_docs),
            message=f"{clean_docs}/{total_docs} public symbols have docstrings",
        ),
        QualityCheck(
            id="missing-annotations",
            passed=clean_annotations == total_annotations,
            score=_score_from_ratio(clean_annotations, total_annotations),
            message=(
                f"{clean_annotations}/{total_annotations} public functions "
                "have full type annotations"
            ),
        ),
        QualityCheck(
            id="long-functions",
            passed=clean_length == total_length,
            score=_score_from_ratio(clean_length, total_length),
            message=(
                f"{clean_length}/{total_length} functions are within {MAX_FUNCTION_LINES} lines"
            ),
        ),
    ]
    return checks


def _check_complexity(files: list[tuple[Path, str]]) -> list[QualityCheck]:
    clean_nesting = 0
    total_nesting = 0

    for filepath, rel in files:
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.body:
                    continue
                total_nesting += 1
                depth = _max_nesting_depth(node.body)
                if depth <= MAX_NESTING_DEPTH:
                    clean_nesting += 1

    checks = [
        QualityCheck(
            id="deep-nesting",
            passed=clean_nesting == total_nesting,
            score=_score_from_ratio(clean_nesting, total_nesting),
            message=(
                f"{clean_nesting}/{total_nesting} functions have max nesting <= {MAX_NESTING_DEPTH}"
            ),
        ),
    ]
    return checks


def _check_safety(files: list[tuple[Path, str]]) -> list[QualityCheck]:
    clean_except = 0
    total_except = 0
    clean_dangerous = 0
    total_files = 0
    clean_assert = 0
    total_nontest = 0

    for filepath, rel in files:
        total_files += 1
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        dangerous = False
        has_assert = False

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                total_except += 1
                if node.type is not None:
                    clean_except += 1
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval"):
                    dangerous = True
            if isinstance(node, ast.Assert):
                has_assert = True

        if not dangerous:
            clean_dangerous += 1

        if not _is_test_file(rel):
            total_nontest += 1
            if not has_assert:
                clean_assert += 1

    checks = [
        QualityCheck(
            id="bare-except",
            passed=clean_except == total_except,
            score=_score_from_ratio(clean_except, total_except),
            message=f"{clean_except}/{total_except} except handlers are typed",
        ),
        QualityCheck(
            id="dangerous-apis",
            passed=clean_dangerous == total_files,
            score=_score_from_ratio(clean_dangerous, total_files, multiplier=3.0),
            message=f"{clean_dangerous}/{total_files} files have no exec/eval calls",
        ),
        QualityCheck(
            id="assert-in-nontest",
            passed=clean_assert == total_nontest,
            score=_score_from_ratio(clean_assert, total_nontest),
            message=f"{clean_assert}/{total_nontest} non-test files have no assert statements",
        ),
    ]
    return checks


def _check_hygiene(files: list[tuple[Path, str]]) -> list[QualityCheck]:
    clean_large = 0
    total_large = 0
    violations_todos = 0
    total_kloc = 0
    clean_prints = 0
    total_prints = 0
    clean_wildcard = 0
    total_wildcard = 0

    for filepath, rel in files:
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        total_large += 1
        line_count = _count_source_lines(filepath)
        if line_count <= MAX_FILE_LINES:
            clean_large += 1

        total_wildcard += 1
        has_wildcard = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(n.name == "*" for n in node.names):
                has_wildcard = True
                break
        if not has_wildcard:
            clean_wildcard += 1

        lines = source.splitlines()
        for line in lines:
            if _TODO_PATTERN.search(line):
                violations_todos += 1
        total_kloc += line_count / 1000.0

        if not _is_excluded_dir(rel) and rel != "__main__.py":
            total_prints += 1
            has_stray_print = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id == "print":
                        if not _is_in_main_guard(node, tree):
                            has_stray_print = True
                            break
            if not has_stray_print:
                clean_prints += 1

    if total_kloc <= 0:
        score_todos = int(max(0.0, 100.0 - violations_todos * 10.0))
    else:
        ratio = violations_todos / total_kloc
        score_todos = int(max(0.0, 100.0 - ratio * 100.0 * 10.0))

    checks = [
        QualityCheck(
            id="large-files",
            passed=clean_large == total_large,
            score=_score_from_ratio(clean_large, total_large),
            message=f"{clean_large}/{total_large} files are within {MAX_FILE_LINES} lines",
        ),
        QualityCheck(
            id="todos",
            passed=violations_todos == 0,
            score=score_todos,
            message=f"Found {violations_todos} TODO/FIXME/HACK/XXX markers",
        ),
        QualityCheck(
            id="stray-prints",
            passed=clean_prints == total_prints,
            score=_score_from_ratio(clean_prints, total_prints, multiplier=2.0),
            message=f"{clean_prints}/{total_prints} non-excluded files have no stray print() calls",
        ),
        QualityCheck(
            id="wildcard-imports",
            passed=clean_wildcard == total_wildcard,
            score=_score_from_ratio(clean_wildcard, total_wildcard),
            message=f"{clean_wildcard}/{total_wildcard} files have no wildcard imports",
        ),
    ]
    return checks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan(
    target_path: Path,
    ignore_dirs: Optional[list[str]] = None,
) -> QualityReport:
    """Run a deterministic quality scan of the repository at *target_path*.

    Args:
        target_path: Absolute path to the root of the repository to scan.
        ignore_dirs: Optional list of directory *names* to skip during the
            walk.  Merged with :data:`_EXCLUDED_DIRS`.

    Returns:
        A fully populated :class:`~orchestrator.schemas.quality.QualityReport`
        instance.  Does **not** write any files — callers are responsible for
        persistence.

    Note:
        This scanner is designed to run against the root of a project.
        Scanning isolated subdirectories may produce inaccurate results
        for checks that depend on project-layout heuristics
        (test detection, script detection, etc.).
    """
    target = Path(target_path).resolve()

    _ignore: frozenset[str] = _EXCLUDED_DIRS
    if ignore_dirs:
        _ignore = _ignore | frozenset(ignore_dirs)

    files = _collect_python_files(target, _ignore)

    readability_checks = _check_readability(files)
    complexity_checks = _check_complexity(files)
    safety_checks = _check_safety(files)
    hygiene_checks = _check_hygiene(files)

    dimensions = {
        "readability": QualityDimension(
            name="readability",
            score=int(sum(c.score for c in readability_checks) / len(readability_checks)),
            checks=readability_checks,
        ),
        "complexity": QualityDimension(
            name="complexity",
            score=int(sum(c.score for c in complexity_checks) / len(complexity_checks)),
            checks=complexity_checks,
        ),
        "safety": QualityDimension(
            name="safety",
            score=int(sum(c.score for c in safety_checks) / len(safety_checks)),
            checks=safety_checks,
        ),
        "hygiene": QualityDimension(
            name="hygiene",
            score=int(sum(c.score for c in hygiene_checks) / len(hygiene_checks)),
            checks=hygiene_checks,
        ),
    }

    overall_score = int(sum(d.score for d in dimensions.values()) / len(dimensions))

    return QualityReport(
        overall_score=overall_score,
        dimensions=dimensions,
    )
