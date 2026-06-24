"""GitHub API client for PatchForge CI/CD integration."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import List, Optional

from github import Github, GithubException

logger = logging.getLogger(__name__)

__all__ = ["GitHubClient", "Issue", "PR"]


@dataclass
class Issue:
    number: int
    title: str
    body: str
    labels: list[str]
    repo: str


@dataclass
class PR:
    number: int
    title: str
    body: str
    head: str
    state: str


class GitHubClient:
    def __init__(self, token: str, repo: str) -> None:
        self._gh = Github(token, timeout=5)
        self._repo = self._gh.get_repo(repo)

    def get_issue(self, issue_number: int) -> Issue:
        gh_issue = self._repo.get_issue(issue_number)
        return Issue(
            number=gh_issue.number,
            title=gh_issue.title,
            body=gh_issue.body or "",
            labels=[label.name for label in gh_issue.labels],
            repo=self._repo.full_name,
        )

    def comment_on_issue(self, issue_number: int, body: str) -> None:
        self._repo.get_issue(issue_number).create_comment(body)

    def add_label(self, issue_number: int, label: str) -> None:
        self._repo.get_issue(issue_number).add_to_labels(label)

    def get_pr_for_branch(self, branch: str) -> Optional[PR]:
        for pr in self._repo.get_pulls(head=branch):
            return PR(
                number=pr.number,
                title=pr.title,
                body=pr.body or "",
                head=pr.head.ref,
                state=pr.state,
            )
        return None

    def list_open_pulls(self) -> List[PR]:
        return [
            PR(
                number=pr.number,
                title=pr.title,
                body=pr.body or "",
                head=pr.head.ref,
                state=pr.state,
            )
            for pr in self._repo.get_pulls(state="open")
        ]

    def create_pr(self, title: str, body: str, head: str, base: str = "main") -> PR:
        pr = self._with_retry(self._repo.create_pull, title=title, body=body, head=head, base=base)
        return PR(
            number=pr.number,
            title=pr.title,
            body=pr.body or "",
            head=pr.head.ref,
            state=pr.state,
        )

    def close_pr(self, pr_number: int) -> None:
        pr = self._repo.get_pull(pr_number)
        if pr.state == "open":
            pr.edit(state="closed")

    def existing_pr_for_webhook(self, issue_number: int) -> Optional[PR]:
        suffix = f"/issue_{issue_number}"
        for pr in self._repo.get_pulls(state="open").get_page(0):
            if pr.head.ref.endswith(suffix):
                return PR(
                    number=pr.number,
                    title=pr.title,
                    body=pr.body or "",
                    head=pr.head.ref,
                    state=pr.state,
                )
        return None

    def _existing_pr_for_recovery(self, issue_number: int) -> Optional[PR]:
        suffix = f"/issue_{issue_number}"
        for pr in self._repo.get_pulls(state="open"):
            if pr.head.ref.endswith(suffix):
                return PR(
                    number=pr.number,
                    title=pr.title,
                    body=pr.body or "",
                    head=pr.head.ref,
                    state=pr.state,
                )
        return None

    def _with_retry(self, fn, *args, max_retries: int = 3, **kwargs):
        for attempt in range(max_retries):
            try:
                return fn(*args, **kwargs)
            except (GithubException, ConnectionError, TimeoutError) as e:
                if isinstance(e, GithubException):
                    if e.status == 403 and "rate limit" in str(e).lower():
                        wait = int(e.headers.get("Retry-After", 60))
                        logger.warning(
                            "Rate limited, retrying in %ss (attempt %d/%d)",
                            wait,
                            attempt + 1,
                            max_retries,
                        )
                        time.sleep(wait + random.uniform(0, 5))
                        continue
                    raise
                logger.warning(
                    "Network error: %s, retrying (attempt %d/%d)",
                    e,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(2**attempt + random.uniform(0, 1))
                continue
        raise RuntimeError(f"Max retries ({max_retries}) exceeded")
