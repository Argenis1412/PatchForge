"""Pre-diff content validation for executor output."""

from __future__ import annotations

import ast
import re

_FENCE_LINE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*[\w+#.\-]*\s*$")


def strip_fences(content: str) -> str:
    """Strip a single outermost markdown code fence wrapping ``content``.

    Handles ``` and ~~~ fences, optional language tags (including tags with
    special characters like ``c++`` or ``f#``), preamble text before the
    opening fence, and trailing text after the closing fence.

    Only strips when exactly one complete fence pair is found — content with
    zero, unclosed, mismatched, or multiple fence pairs is returned unchanged
    to avoid corrupting legitimate content (e.g. inner backticks in a
    docstring, or multiple genuine code blocks).
    """
    lines = content.split("\n")

    fence_type: str | None = None
    is_open = False
    pair_start: int | None = None
    pairs: list[tuple[int, int]] = []

    for i, line in enumerate(lines):
        match = _FENCE_LINE_RE.match(line)
        if match is None:
            continue
        marker = match.group(1)[0]  # "`" or "~"
        if not is_open:
            fence_type = marker
            pair_start = i
            is_open = True
        elif marker == fence_type:
            pairs.append((pair_start, i))
            is_open = False
            fence_type = None
            pair_start = None
        # else: mismatched marker while open — not a valid delimiter here, ignore

    if len(pairs) != 1:
        return content

    start, end = pairs[0]
    return "\n".join(lines[start + 1 : end]).strip()


def validate_python_content(
    modified_content: str,
    original_content: str,
    filename: str,
) -> str | None:
    """Return an error string if modified_content is not valid Python, None otherwise.

    Only rejects when the original file parses successfully but the modified
    content does not — avoids false positives when fixing pre-existing syntax errors.
    """
    try:
        ast.parse(modified_content, filename=filename)
        return None
    except SyntaxError as mod_err:
        try:
            ast.parse(original_content, filename=filename)
        except SyntaxError:
            return None
        line_info = f" (line {mod_err.lineno})" if mod_err.lineno else ""
        return f"LLM output is not valid Python{line_info}: {mod_err.msg}"
