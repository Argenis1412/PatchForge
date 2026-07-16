"""Tests for git config user.name/user.email wrappers (P4-5)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.git import git_config_user_email, git_config_user_name


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
