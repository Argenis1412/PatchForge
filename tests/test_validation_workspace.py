import subprocess
from pathlib import Path

import pytest

from orchestrator.validation_workspace import (
    apply_patch_to_copy,
    cleanup_temp_copy,
    create_temp_copy,
    create_validation_workspace,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    test_file = path / "main.py"
    test_file.write_text("print('Hello')\n")
    subprocess.run(["git", "add", "main.py"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"], cwd=path, check=True, capture_output=True
    )


@pytest.fixture
def repo_with_dirs(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / ".venv").mkdir()
    (repo / "__pycache__").mkdir()
    (repo / ".pytest_cache").mkdir()
    (repo / ".ruff_cache").mkdir()
    (repo / "workspace").mkdir()
    return repo


def test_create_temp_copy_excludes_git(repo_with_dirs: Path):
    temp_dir = create_temp_copy(repo_with_dirs)
    assert not (temp_dir / ".git").exists()
    assert (temp_dir / "main.py").exists()
    cleanup_temp_copy(temp_dir)


def test_create_temp_copy_excludes_venv(repo_with_dirs: Path):
    temp_dir = create_temp_copy(repo_with_dirs)
    assert not (temp_dir / ".venv").exists()
    cleanup_temp_copy(temp_dir)


def test_create_temp_copy_excludes_cache(repo_with_dirs: Path):
    temp_dir = create_temp_copy(repo_with_dirs)
    assert not (temp_dir / "__pycache__").exists()
    assert not (temp_dir / ".pytest_cache").exists()
    assert not (temp_dir / ".ruff_cache").exists()
    cleanup_temp_copy(temp_dir)


def test_apply_patch_to_copy(repo_with_dirs: Path):
    temp_dir = create_temp_copy(repo_with_dirs)
    # Initialize git in temp copy so we can apply patch properly
    subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)

    patch_file = temp_dir / "patch.diff"
    patch_content = """diff --git a/main.py b/main.py
--- a/main.py
+++ b/main.py
@@ -1 +1 @@
-print('Hello')
+print('World')
"""
    patch_file.write_text(patch_content)
    result = apply_patch_to_copy(temp_dir, patch_file)
    assert result.return_code == 0
    assert (temp_dir / "main.py").read_text() == "print('World')\n"
    cleanup_temp_copy(temp_dir)


def test_apply_patch_does_not_modify_original(repo_with_dirs: Path):
    temp_dir = create_temp_copy(repo_with_dirs)
    subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)

    patch_file = temp_dir / "patch.diff"
    patch_content = """diff --git a/main.py b/main.py
--- a/main.py
+++ b/main.py
@@ -1 +1 @@
-print('Hello')
+print('World')
"""
    patch_file.write_text(patch_content)
    apply_patch_to_copy(temp_dir, patch_file)

    assert (repo_with_dirs / "main.py").read_text() == "print('Hello')\n"
    cleanup_temp_copy(temp_dir)


def test_cleanup_deletes_temp(repo_with_dirs: Path):
    temp_dir = create_temp_copy(repo_with_dirs)
    assert temp_dir.exists()
    cleanup_temp_copy(temp_dir)
    assert not temp_dir.exists()


def test_cleanup_ignores_missing(tmp_path: Path):
    missing_dir = tmp_path / "does_not_exist"
    cleanup_temp_copy(missing_dir)  # Should not raise error


def test_validation_workspace_context_manager(repo_with_dirs: Path):
    patch_path = repo_with_dirs / "patch.diff"

    with create_validation_workspace(repo_with_dirs, patch_path) as workspace:
        temp_root = workspace.temporary_root
        assert temp_root.exists()
        assert not (temp_root / ".git").exists()
        assert workspace.original_root == repo_with_dirs
        assert workspace.patch_path == patch_path

    assert not temp_root.exists()
