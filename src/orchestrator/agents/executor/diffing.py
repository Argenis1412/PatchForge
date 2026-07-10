"""Unified diff generation utility for executor changes."""

from __future__ import annotations

import difflib


def _make_diff(original: str, modified: str, filename: str, *, is_new_file: bool = False) -> str:
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    fromfile = "/dev/null" if is_new_file else f"a/{filename}"

    diff_lines = list(
        difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=fromfile,
            tofile=f"b/{filename}",
        )
    )
    return "".join(diff_lines)
