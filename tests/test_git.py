"""Tests for git config user.name/user.email wrappers (P4-5)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.git import (
    apply_patch,
    commit_candidate,
    create_controlled_branch,
    git_config_user_email,
    git_config_user_name,
    promote_candidate,
)


def _init_git_repo(
    path: Path, *, name: str | None = "Test User", email: str | None = "test@example.com"
) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    if name is not None:
        subprocess.run(
            ["git", "config", "user.name", name], cwd=path, check=True, capture_output=True
        )
    if email is not None:
        subprocess.run(
            ["git", "config", "user.email", email], cwd=path, check=True, capture_output=True
        )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    return repo


@pytest.fixture
def git_repo_no_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo with no local user.name/user.email, isolated from the
    machine's real global/system git config so the wrapper's fallback
    behavior (local -> global -> system) can be tested deterministically."""
    repo = tmp_path / "repo_no_identity"
    repo.mkdir()
    _init_git_repo(repo, name=None, email=None)
    empty_config = tmp_path / "empty_gitconfig"
    empty_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_config))
    return repo


def test_git_config_user_name_returns_value(git_repo: Path):
    assert git_config_user_name(git_repo) == "Test User"


def test_git_config_user_email_returns_value(git_repo: Path):
    assert git_config_user_email(git_repo) == "test@example.com"


def test_git_config_user_name_returns_none_when_unset(git_repo_no_identity: Path):
    assert git_config_user_name(git_repo_no_identity) is None


def test_git_config_user_email_returns_none_when_unset(git_repo_no_identity: Path):
    assert git_config_user_email(git_repo_no_identity) is None


def test_git_config_user_name_returns_none_when_empty(git_repo: Path):
    subprocess.run(
        ["git", "config", "user.name", ""], cwd=git_repo, check=True, capture_output=True
    )
    assert git_config_user_name(git_repo) is None


def test_git_config_user_email_returns_none_when_empty(git_repo: Path):
    subprocess.run(
        ["git", "config", "user.email", ""], cwd=git_repo, check=True, capture_output=True
    )
    assert git_config_user_email(git_repo) is None


def test_git_config_user_name_returns_none_on_timeout(tmp_path: Path):
    with patch(
        "orchestrator.git.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
    ):
        assert git_config_user_name(tmp_path) is None


def test_git_config_user_email_returns_none_on_timeout(tmp_path: Path):
    with patch(
        "orchestrator.git.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
    ):
        assert git_config_user_email(tmp_path) is None


def test_git_config_user_name_returns_none_when_no_git_binary(tmp_path: Path):
    with patch("orchestrator.git.subprocess.run", side_effect=FileNotFoundError):
        assert git_config_user_name(tmp_path) is None


def test_git_config_user_email_returns_none_when_no_git_binary(tmp_path: Path):
    with patch("orchestrator.git.subprocess.run", side_effect=FileNotFoundError):
        assert git_config_user_email(tmp_path) is None


def test_create_controlled_branch_returns_timeout_result(tmp_path: Path):
    with patch(
        "orchestrator.git.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
    ):
        result = create_controlled_branch(tmp_path, "patchforge/test", timeout=1)
    assert result.return_code == 124


def test_apply_patch_returns_timeout_result(tmp_path: Path):
    with patch(
        "orchestrator.git.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
    ):
        result = apply_patch(tmp_path, tmp_path / "patch.diff", timeout=1)
    assert result.return_code == 124


def test_promote_candidate_rejects_invalid_ref_before_update_ref(git_repo: Path):
    result = promote_candidate(
        git_repo,
        base_ref="refs/heads/main\ncreate refs/heads/injected",
        base_commit="a" * 40,
        candidate_ref="refs/heads/patchforge/run",
        candidate_commit="b" * 40,
        receipt_ref="refs/patchforge/promotions/run",
    )

    assert result.return_code != 0
    assert "invalid ref" in result.stderr


def test_commit_candidate_uses_explicit_patch_and_git_timeouts(git_repo: Path):
    completed = subprocess.CompletedProcess(args=["git"], returncode=0, stdout="", stderr="")
    with (
        patch("orchestrator.git._run_git_safe", return_value=completed) as run_git,
        patch("orchestrator.git.current_head", return_value="a" * 40) as head,
    ):
        result = commit_candidate(
            git_repo,
            git_repo / "patch.diff",
            "message",
            git_timeout=41,
            patch_timeout=42,
        )

    assert result == "a" * 40
    assert [call.kwargs["timeout"] for call in run_git.call_args_list] == [42, 42, 41, 41]
    assert head.call_args.kwargs["timeout"] == 41
