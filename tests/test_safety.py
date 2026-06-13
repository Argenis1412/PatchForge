import os
from pathlib import Path

import pytest

from orchestrator.safety import ensure_safe_relative, validate_filename


def test_validate_filename_rejects_empty():
    with pytest.raises(ValueError, match="must not be empty"):
        validate_filename("")


def test_validate_filename_rejects_absolute():
    with pytest.raises(ValueError, match="absolute"):
        validate_filename("/etc/passwd")


def test_validate_filename_rejects_traversal():
    with pytest.raises(ValueError, match="directory traversal"):
        validate_filename("../../etc/passwd")


def test_validate_filename_rejects_null_byte():
    with pytest.raises(ValueError, match="null"):
        validate_filename("file\0.txt")


def test_validate_filename_accepts_normal():
    result = validate_filename("sub/foo.py")
    assert result == "sub/foo.py"


def test_ensure_safe_relative_rejects_empty():
    with pytest.raises(ValueError, match="must not be empty"):
        ensure_safe_relative("", Path("/base"))


def test_ensure_safe_relative_rejects_absolute():
    with pytest.raises(ValueError, match="absolute"):
        ensure_safe_relative("/etc", Path("/base"))


def test_ensure_safe_relative_rejects_traversal():
    with pytest.raises(ValueError, match="parent directory traversal"):
        ensure_safe_relative("../../etc", Path("/base"))


def test_ensure_safe_relative_accepts_normal():
    result = ensure_safe_relative("sub/file.py", Path("/base"))
    assert result == "sub/file.py"


@pytest.mark.skipif(
    os.name == "nt", reason="symlink creation requires admin/Developer Mode on Windows"
)
def test_ensure_safe_relative_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    inside = tmp_path / "inside"
    inside.mkdir()
    link = inside / "escape"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes base"):
        ensure_safe_relative("escape", inside)


def test_ensure_safe_relative_nonexistent_base():
    with pytest.raises(ValueError, match="parent directory traversal"):
        ensure_safe_relative("../../etc", Path("/nonexistent_12345"))


def test_validate_filename_rejects_windows_absolute():
    with pytest.raises(ValueError, match="absolute"):
        validate_filename("C:\\foo\\bar.py")


def test_validate_filename_rejects_windows_traversal():
    with pytest.raises(ValueError, match="directory traversal"):
        validate_filename("..\\..\\etc\\passwd")


def test_ensure_safe_relative_rejects_parent_segment():
    with pytest.raises(ValueError, match="parent directory traversal"):
        ensure_safe_relative("sub/../file.py", Path("/base"))
