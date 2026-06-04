import subprocess
from pathlib import Path

import pytest

from orchestrator.git import (
    apply_patch,
    check_patch,
    create_controlled_branch,
    current_branch,
    current_head,
    force_reset_apply,
    is_git_repo,
    is_working_tree_clean,
    repository_state,
    resolve_git_root,
    revert_apply,
    working_tree_status,
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
    # create initial commit
    test_file = path / "README.md"
    test_file.write_text("Hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"], cwd=path, check=True, capture_output=True
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    return repo


def test_is_git_repo_true(git_repo: Path):
    assert is_git_repo(git_repo)


def test_is_git_repo_false(tmp_path: Path):
    assert not is_git_repo(tmp_path)


def test_resolve_git_root(git_repo: Path):
    sub = git_repo / "sub"
    sub.mkdir()
    assert resolve_git_root(sub) == git_repo.resolve()


def test_current_branch_main(git_repo: Path):
    # Depending on git config, might be master or main. Let's assume it returns a string
    branch = current_branch(git_repo)
    assert branch in ("main", "master")


def test_current_head_returns_sha(git_repo: Path):
    head = current_head(git_repo)
    assert len(head) == 40


def test_is_working_tree_clean_true(git_repo: Path):
    assert is_working_tree_clean(git_repo)


def test_is_working_tree_clean_false(git_repo: Path):
    (git_repo / "README.md").write_text("modified\n")
    assert not is_working_tree_clean(git_repo)


def test_working_tree_status_dirty(git_repo: Path):
    (git_repo / "README.md").write_text("modified\n")
    status = working_tree_status(git_repo)
    assert not status.is_clean
    assert "M README.md" in status.porcelain


def test_check_patch_valid(git_repo: Path):
    patch_file = git_repo / "patch.diff"
    patch_content = """diff --git a/README.md b/README.md
index 980a0d5..16c4ab0 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-Hello
+Hello World
"""
    patch_file.write_text(patch_content)
    result = check_patch(git_repo, patch_file)
    assert result.return_code == 0


def test_check_patch_invalid(git_repo: Path):
    patch_file = git_repo / "patch.diff"
    patch_content = """diff --git a/README.md b/README.md
index 980a0d5..16c4ab0 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-Wrong
+Hello World
"""
    patch_file.write_text(patch_content)
    result = check_patch(git_repo, patch_file)
    assert result.return_code != 0


def test_create_controlled_branch(git_repo: Path):
    result = create_controlled_branch(git_repo, "test-branch")
    assert result.return_code == 0
    assert current_branch(git_repo) == "test-branch"


def test_apply_patch(git_repo: Path):
    patch_file = git_repo / "patch.diff"
    patch_content = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-Hello
+Hello World
"""
    patch_file.write_text(patch_content)
    result = apply_patch(git_repo, patch_file)
    assert result.return_code == 0
    assert (git_repo / "README.md").read_text() == "Hello World\n"


def test_revert_apply(git_repo: Path):
    (git_repo / "README.md").write_text("Modified")
    result = revert_apply(git_repo)
    assert result.return_code == 0
    assert (git_repo / "README.md").read_text() == "Hello\n"


def test_repository_state(git_repo: Path):
    state = repository_state(git_repo)
    assert state.is_clean
    assert len(state.head) == 40
    assert state.branch in ("master", "main")


def test_force_reset_apply_restores_exact_state(git_repo: Path):
    head_before = current_head(git_repo)
    # Track original file content
    original_readme = (git_repo / "README.md").read_text()

    # Modify existing file
    (git_repo / "README.md").write_text("Modified\n")
    # Create new file
    (git_repo / "new_file.py").write_text("print('new')\n")
    # Delete a file (create one first, then delete it)
    (git_repo / "to_delete.md").write_text("delete me\n")
    (git_repo / "to_delete.md").unlink()
    # Create new directory with file
    new_dir = git_repo / "new_dir"
    new_dir.mkdir()
    (new_dir / "inner.py").write_text("x = 1\n")

    # Verify working tree is dirty
    assert not is_working_tree_clean(git_repo)

    # Execute force reset
    result = force_reset_apply(git_repo, head_before)

    # Verify reset succeeded
    assert result.return_code == 0

    # Verify SHA is unchanged
    assert current_head(git_repo) == head_before

    # Verify working tree is clean
    assert is_working_tree_clean(git_repo)

    # Verify original file content is restored
    assert (git_repo / "README.md").read_text() == original_readme

    # Verify new file was removed by clean -fd
    assert not (git_repo / "new_file.py").exists()

    # Verify new directory was removed by clean -fd
    assert not (git_repo / "new_dir").exists()


def test_non_git_dir_reported_clearly(tmp_path: Path):
    with pytest.raises(ValueError, match="Not a Git repository"):
        repository_state(tmp_path)
