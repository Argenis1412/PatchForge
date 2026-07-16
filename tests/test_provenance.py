"""Tests for the Approval Provenance domain module (P4-5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from orchestrator.provenance import resolve_approved_by, resolve_triggered_by


def test_resolve_triggered_by_ci_path():
    assert resolve_triggered_by(github_actor="octocat") == "github:octocat"


def test_resolve_triggered_by_local_path():
    with (
        patch("orchestrator.provenance.git_config_user_name", return_value="Alice"),
        patch("orchestrator.provenance.git_config_user_email", return_value="alice@example.com"),
    ):
        assert resolve_triggered_by(repo_root=Path(".")) == "local:Alice <alice@example.com>"


def test_resolve_triggered_by_ci_takes_precedence():
    with (
        patch("orchestrator.provenance.git_config_user_name", return_value="Alice"),
        patch("orchestrator.provenance.git_config_user_email", return_value="alice@example.com"),
    ):
        result = resolve_triggered_by(repo_root=Path("."), github_actor="octocat")
    assert result == "github:octocat"


def test_resolve_triggered_by_nothing_set():
    assert resolve_triggered_by() is None


def test_resolve_triggered_by_empty_git_config():
    with (
        patch("orchestrator.provenance.git_config_user_name", return_value=None),
        patch("orchestrator.provenance.git_config_user_email", return_value=None),
    ):
        assert resolve_triggered_by(repo_root=Path(".")) is None


def test_resolve_triggered_by_name_only():
    with (
        patch("orchestrator.provenance.git_config_user_name", return_value="Alice"),
        patch("orchestrator.provenance.git_config_user_email", return_value=None),
    ):
        assert resolve_triggered_by(repo_root=Path(".")) == "local:Alice"


def test_resolve_triggered_by_email_only():
    with (
        patch("orchestrator.provenance.git_config_user_name", return_value=None),
        patch("orchestrator.provenance.git_config_user_email", return_value="alice@example.com"),
    ):
        assert resolve_triggered_by(repo_root=Path(".")) == "local:<alice@example.com>"


def test_resolve_approved_by():
    with (
        patch("orchestrator.provenance.git_config_user_name", return_value="Alice"),
        patch("orchestrator.provenance.git_config_user_email", return_value="alice@example.com"),
    ):
        assert resolve_approved_by(Path(".")) == "local:Alice <alice@example.com>"


def test_resolve_approved_by_no_config():
    with (
        patch("orchestrator.provenance.git_config_user_name", return_value=None),
        patch("orchestrator.provenance.git_config_user_email", return_value=None),
    ):
        assert resolve_approved_by(Path(".")) is None
