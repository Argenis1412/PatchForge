"""Collect target repo file listing for Architect prompt injection (D-001 root cause fix)."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.schemas.config import TargetConfig

_EXTRA_IGNORE_DIRS = frozenset(
    {
        "dist",
        "build",
        "htmlcov",
        ".tox",
        ".mypy_cache",
        ".coverage",
        "coverage",
        "__pypackages__",
        ".eggs",
    }
)

_DOCSTRING_MAX_LEN = 80
_NAMES_CAP = 8
_ANNOTATION_BUDGET = 10_000


def _summarize_python_file(filepath: Path) -> str | None:
    """Return a short annotation for a Python file, or ``None`` if nothing useful."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None

    docstring = ast.get_docstring(tree)
    if docstring:
        docstring = " ".join(docstring.split())
        if len(docstring) > _DOCSTRING_MAX_LEN:
            docstring = docstring[: _DOCSTRING_MAX_LEN - 1] + "…"

    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(f"{node.name}()")
        elif isinstance(node, ast.ClassDef):
            names.append(node.name)
        if len(names) >= _NAMES_CAP:
            break

    if not docstring and not names:
        return None

    parts: list[str] = []
    if docstring:
        parts.append(docstring)
    if names:
        parts.append(", ".join(names))
    return " | ".join(parts)


def _detect_packages(paths: list[str]) -> set[str]:
    """Return directory prefixes that contain an ``__init__.py``."""
    package_dirs: set[str] = set()
    for p in paths:
        if p == "__init__.py":
            package_dirs.add("")
        elif p.endswith("/__init__.py"):
            package_dirs.add(p.rsplit("/", 1)[0])
    return package_dirs


def collect_target_files(
    config: TargetConfig | None,
    max_paths: int = 500,
) -> tuple[list[str], bool, int, set[str]]:
    """Return ``(paths, truncated, total_before_truncation, package_dirs)``.

    *paths* are POSIX-relative to ``config.target_path``, sorted alphabetically.
    Honors ``config.ignore_dirs`` plus ``_EXTRA_IGNORE_DIRS``.
    Lists **all** files regardless of extension.
    *package_dirs* is detected **before** truncation so packages near the end
    of the alphabet are still recognized.
    Returns ``([], False, 0, set())`` when *config* is ``None``.
    """
    if config is None:
        return [], False, 0, set()

    target_path = Path(config.target_path).resolve()
    ignore_set = set(config.ignore_dirs) | _EXTRA_IGNORE_DIRS

    all_paths: list[str] = []
    for root, dirs, files in os.walk(target_path):
        dirs[:] = [d for d in dirs if d not in ignore_set]
        root_path = Path(root)
        for fname in files:
            rel = (root_path / fname).relative_to(target_path).as_posix()
            all_paths.append(rel)

    all_paths.sort()
    total = len(all_paths)

    package_dirs = _detect_packages(all_paths)

    truncated = total > max_paths
    if truncated:
        all_paths = all_paths[:max_paths]

    return all_paths, truncated, total, package_dirs


def build_target_files_block(
    config: TargetConfig | None,
) -> tuple[str, list[str], bool, int]:
    """Format the ``[TARGET FILES]`` block for prompt injection.

    Returns ``(block_text, paths, truncated, total)`` so callers can log stats
    without triggering a second directory walk.
    """
    paths, truncated, total, package_dirs = collect_target_files(config)

    if config is None:
        return "[TARGET FILES]\n(unavailable — no target config provided)", [], False, 0

    if not paths:
        return "[TARGET FILES]\n(no files found in target directory)", [], False, 0

    target_path = Path(config.target_path).resolve()

    lines: list[str] = ["[TARGET FILES]"]

    if truncated:
        lines.append(f"(truncated: showing {len(paths)} of {total} paths, alphabetical order)")
        top_dirs = sorted({p.split("/")[0] + "/" for p in paths if "/" in p})
        if top_dirs:
            lines.append(f"(top-level dirs present: {', '.join(top_dirs)})")

    budget_remaining = _ANNOTATION_BUDGET
    for p in paths:
        if budget_remaining > 0 and p.endswith(".py"):
            parent = p.rsplit("/", 1)[0] if "/" in p else ""
            if parent in package_dirs:
                annotation = _summarize_python_file(target_path / p)
                if annotation and len(annotation) <= budget_remaining:
                    lines.append(f"{p}  # {annotation}")
                    budget_remaining -= len(annotation)
                    continue
        lines.append(p)

    return "\n".join(lines), paths, truncated, total
