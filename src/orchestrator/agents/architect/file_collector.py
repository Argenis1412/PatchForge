"""Collect target repo file listing for Architect prompt injection (D-001 root cause fix)."""

from __future__ import annotations

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


def collect_target_files(
    config: TargetConfig | None,
    max_paths: int = 500,
) -> tuple[list[str], bool, int]:
    """Return ``(paths, truncated, total_before_truncation)``.

    *paths* are POSIX-relative to ``config.target_path``, sorted alphabetically.
    Honors ``config.ignore_dirs`` plus ``_EXTRA_IGNORE_DIRS``.
    Lists **all** files regardless of extension.
    Returns ``([], False, 0)`` when *config* is ``None``.
    """
    if config is None:
        return [], False, 0

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
    truncated = total > max_paths

    if truncated:
        all_paths = all_paths[:max_paths]

    return all_paths, truncated, total


def build_target_files_block(config: TargetConfig | None) -> str:
    """Format the ``[TARGET FILES]`` block for prompt injection."""
    paths, truncated, total = collect_target_files(config)

    if config is None:
        return "[TARGET FILES]\n(unavailable — no target config provided)"

    if not paths:
        return "[TARGET FILES]\n(no files found in target directory)"

    lines: list[str] = ["[TARGET FILES]"]

    if truncated:
        lines.append(f"(truncated: showing {len(paths)} of {total} paths, alphabetical order)")
        top_dirs = sorted({p.split("/")[0] + "/" for p in paths if "/" in p})
        if top_dirs:
            lines.append(f"(top-level dirs present: {', '.join(top_dirs)})")

    lines.extend(paths)
    return "\n".join(lines)
