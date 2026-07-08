"""Pre-diff content validation for executor output."""

from __future__ import annotations

import ast


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
