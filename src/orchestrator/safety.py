from __future__ import annotations

from pathlib import Path, PurePath


def validate_filename(name: str) -> str:
    if not name:
        raise ValueError("Filename must not be empty")
    if PurePath(name).is_absolute():
        raise ValueError(f"Filename must be a relative path, got absolute: {name!r}")
    if ".." in PurePath(name).parts:
        raise ValueError(f"Filename must not contain parent directory traversal: {name!r}")
    if "\0" in name:
        raise ValueError("Filename must not contain null bytes")
    return name


def ensure_safe_relative(path: str, base: Path) -> str:
    if not path:
        raise ValueError("Path must not be empty")
    if PurePath(path).is_absolute():
        raise ValueError(f"Path must be relative, got absolute: {path!r}")
    resolved_base = Path(base).resolve()
    candidate = (resolved_base / path).resolve()
    try:
        candidate.relative_to(resolved_base)
    except ValueError:
        raise ValueError(f"Path {path!r} escapes base directory {resolved_base}")
    return path
