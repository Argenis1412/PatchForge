"""Path-safety utilities for preventing directory traversal and unsafe file access.

This module provides helper functions to validate and sanitise file paths before
they are used by the orchestrator, ensuring that no path can escape a designated
base directory or reference absolute locations on the filesystem.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


def _is_absolute_any(p: str) -> bool:
    """Check whether a path string is absolute on any supported platform.

    Evaluates the given path against both POSIX and Windows path conventions,
    returning True if the path is considered absolute under either interpretation.
    This guards against platform-specific absolute path formats being smuggled
    through on systems that use the other convention.

    Parameters
    ----------
    p:
        The path string to evaluate.

    Returns
    -------
    bool
        True if ``p`` is absolute according to POSIX or Windows path rules,
        False otherwise.
    """
    return PurePosixPath(p).is_absolute() or PureWindowsPath(p).is_absolute()


def _has_parent_segment(p: str) -> bool:
    """Check whether any path component is a parent-directory traversal segment.

    Normalises the path string to use forward slashes and then inspects each
    component returned by :class:`pathlib.PurePosixPath`.  If any component is
    the special ``'..'`` segment the function returns True, indicating that the
    path attempts to traverse above its current directory.

    Parameters
    ----------
    p:
        The path string to evaluate.

    Returns
    -------
    bool
        True if ``p`` contains at least one ``'..'`` component, False otherwise.
    """
    norm = p.replace("\\", "/")
    return ".." in PurePosixPath(norm).parts


def validate_filename(name: str) -> str:
    if not name:
        raise ValueError("Filename must not be empty")
    if _is_absolute_any(name):
        raise ValueError(f"Filename must be a relative path, got absolute: {name!r}")
    if _has_parent_segment(name):
        raise ValueError(f"Filename must not contain parent directory traversal: {name!r}")
    if "\0" in name:
        raise ValueError("Filename must not contain null bytes")
    return name


def ensure_safe_relative(path: str, base: Path) -> str:
    if not path:
        raise ValueError("Path must not be empty")
    if _is_absolute_any(path):
        raise ValueError(f"Path must be relative, got absolute: {path!r}")
    if _has_parent_segment(path):
        raise ValueError(f"Path must not contain parent directory traversal: {path!r}")
    resolved_base = Path(base).resolve()
    candidate = (resolved_base / path).resolve()
    try:
        candidate.relative_to(resolved_base)
    except ValueError:
        raise ValueError(f"Path {path!r} escapes base directory {resolved_base}")
    return path
