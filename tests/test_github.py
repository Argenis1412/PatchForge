"""Tests for GitHub client and webhook handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.clients.github import PR, GitHubClient, Issue
from orchestrator.integrations.webhook import handle_issue_opened


@pytest.fixture
def mock_github():
    with patch("orchestrator.clients.github.Github") as mock:
        gh_client = mock.return_value
        repo = MagicMock()
        gh_client.get_repo.return_value = repo
        yield mock, repo


@pytest.fixture
def client(mock_github):
    _, repo = mock_github
    repo.full_name = "owner/repo"
    c = GitHubClient(token="fake-token", repo="owner/repo")
    c._repo = repo
    return c


def _fake_pr(number=1, title="Test", body="body", head="patchforge/run_x/issue_1", state="open"):
    pr = MagicMock()
    pr.number = number
    pr.title = title
    pr.body = body
    pr.head.ref = head
    pr.state = state
    return pr


def _fake_issue(number=1, title="Fix bug", body="details", labels=None):
    issue = MagicMock()
    issue.number = number
    issue.title = title
    issue.body = body
    issue.labels = [MagicMock(name=lb) for lb in (labels or ["bug"])]
    return issue


class TestGetIssue:
    def test_get_issue(self, client):
        fake = _fake_issue()
        client._repo.get_issue.return_value = fake
        result = client.get_issue(1)
        assert isinstance(result, Issue)
        assert result.number == 1
        assert result.title == "Fix bug"
        assert result.repo == "owner/repo"


class TestCreatePR:
    def test_create_pr(self, client):
        fake = _fake_pr()
        client._repo.create_pull.return_value = fake
        result = client.create_pr(title="Test", body="body", head="branch", base="main")
        assert isinstance(result, PR)
        assert result.number == 1
        assert result.title == "Test"

    def test_create_pr_rate_limit_retry(self, client):
        from github import GithubException

        fake = _fake_pr()
        exc = GithubException(403, {"message": "rate limit exceeded"}, headers={"Retry-After": "0"})
        client._repo.create_pull.side_effect = [exc, exc, fake]
        result = client.create_pr(title="Test", body="body", head="branch")
        assert result.number == 1

    def test_create_pr_rate_limit_exhausted(self, client):
        from github import GithubException

        exc = GithubException(403, {"message": "rate limit exceeded"}, headers={"Retry-After": "0"})
        client._repo.create_pull.side_effect = exc
        with pytest.raises(RuntimeError, match="Max retries"):
            client.create_pr(title="Test", body="body", head="branch")

    def test_create_pr_network_error_retry(self, client):
        fake = _fake_pr()
        client._repo.create_pull.side_effect = [ConnectionError("reset"), fake]
        result = client.create_pr(title="Test", body="body", head="branch")
        assert result.number == 1


class TestExistingPrForWebhook:
    def test_found(self, client):
        pr = _fake_pr(head="patchforge/run_abc/issue_42")
        page = MagicMock()
        page.__iter__.return_value = [pr]
        client._repo.get_pulls.return_value.get_page.return_value = page
        result = client.existing_pr_for_webhook(42)
        assert result is not None
        assert result.number == 1

    def test_not_found(self, client):
        pr = _fake_pr(head="patchforge/run_abc/issue_99")
        page = MagicMock()
        page.__iter__.return_value = [pr]
        client._repo.get_pulls.return_value.get_page.return_value = page
        result = client.existing_pr_for_webhook(42)
        assert result is None


class TestHandleIssueOpened:
    @pytest.fixture
    def event(self):
        return {
            "issue": {"number": 42, "title": "Fix"},
            "repository": {"full_name": "owner/repo"},
        }

    @patch("orchestrator.integrations.webhook._sqlite_connect")
    @patch("orchestrator.integrations.webhook.enqueue_issue")
    def test_idempotency_skip(self, mock_enqueue, mock_connect, event, client):
        client.existing_pr_for_webhook = MagicMock(
            return_value=PR(1, "PR", "", "head", "open"),
        )
        result = handle_issue_opened(event, client, Path("/tmp/q.db"))
        assert result["status"] == "skipped"
        mock_enqueue.assert_not_called()

    @patch("orchestrator.integrations.webhook._sqlite_connect")
    @patch("orchestrator.integrations.webhook.enqueue_issue")
    def test_label_failure_does_not_raise(self, mock_enqueue, mock_connect, event, client):
        client.existing_pr_for_webhook = MagicMock(return_value=None)
        mock_enqueue.return_value = "run_123"
        client.add_label = MagicMock(side_effect=ValueError("API error"))
        result = handle_issue_opened(event, client, Path("/tmp/q.db"))
        assert result["status"] == "enqueued"

    @patch("orchestrator.integrations.webhook._sqlite_connect")
    @patch("orchestrator.integrations.webhook.enqueue_issue")
    def test_enqueue_new_issue(self, mock_enqueue, mock_connect, event, client):
        client.existing_pr_for_webhook = MagicMock(return_value=None)
        mock_enqueue.return_value = "run_123"
        result = handle_issue_opened(event, client, Path("/tmp/q.db"))
        assert result["status"] == "enqueued"
        assert result["run_id"] == "run_123"
