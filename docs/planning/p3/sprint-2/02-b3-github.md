# B3 — GitHub Integration

## Goal

Create a GitHub API client and webhook handler so PatchForge can detect issues, open PRs, and manage labels. Idempotency must be guaranteed via branch name (immutable), never via PR body or labels.

---

## Current State

### No GitHub client exists

`src/orchestrator/clients/` contains only LLM API clients:

```
clients/
├── __init__.py
├── anthropic_client.py
├── bootstrap.py
├── gemini_client.py
└── groq_client.py
```

No `github.py`. No webhook handler. No PR creation, issue detection, or rate limit handling.

---

## Changes

### 1. Create `src/orchestrator/clients/github.py`

```python
"""GitHub API client with rate-limit handling and idempotency via branch name."""

import os
import random
import time
from dataclasses import dataclass
from typing import List, Optional

from typing import Any, List, Optional

from github import Github, GithubException
from github.PullRequest import PullRequest


@dataclass
class Issue:
    number: int
    title: str
    body: str
    labels: List[str]
    repo: str


@dataclass
class PR:
    number: int
    title: str
    body: str
    head: Optional[Any] = None
    state: str = "open"


class GitHubClient:
    BRANCH_TEMPLATE = "patchforge/run_{run_id}/issue_{issue_number}"
    PR_TITLE_TEMPLATE = "PatchForge: {goal}"
    COMMIT_TEMPLATE = "PatchForge: {run_id} [skip ci]"
    MAX_PR_BODY_BYTES = 120_000  # 5KB safety margin under GitHub's 125KB limit

    def __init__(self, token: str, repo: str):
        self._gh = Github(token)
        self._repo = self._gh.get_repo(repo)

    def get_issue(self, issue_number: int) -> Issue:
        gh_issue = self._repo.get_issue(issue_number)
        return Issue(
            number=gh_issue.number,
            title=gh_issue.title,
            body=gh_issue.body or "",
            labels=[l.name for l in gh_issue.labels],
            repo=self._repo.full_name,
        )

    def get_pr_for_branch(self, branch: str) -> Optional[PR]:
        """Linearizable check: GET /pulls?head= — branch name is the source of truth."""
        pulls = self._repo.get_pulls(state="open", head=branch)
        for pr in pulls:
            return PR(number=pr.number, title=pr.title, body=pr.body or "",
                      head=pr.head, state=pr.state)
        return None

    def list_open_pulls(self) -> List[PR]:
        """GET /pulls?state=open — used for cross-run_id idempotency."""
        return [
            PR(number=p.number, title=p.title, body=p.body or "", head=p.head, state=p.state)
            for p in self._repo.get_pulls(state="open")
        ]

    # Use canonical _with_retry() — see 00-README.md §Canonical Patterns
    # E.g. self._with_retry(self._repo.create_pull, title=title, ...)

    def create_pr(self, title: str, body: str, head: str, base: str = "main") -> PR:
        pr = self._with_retry(self._repo.create_pull, title=title, body=body, head=head, base=base)
        return PR(number=pr.number, title=pr.title, body=pr.body or "", head=pr.head, state=pr.state)

    def close_pr(self, pr_number: int) -> None:
        pr = self._with_retry(self._repo.get_pull, pr_number)
        self._with_retry(pr.edit, state="closed")

    def comment_on_issue(self, issue_number: int, body: str) -> None:
        issue = self._with_retry(self._repo.get_issue, issue_number)
        self._with_retry(issue.create_comment, body)

    def add_label(self, issue_number: int, label: str) -> None:
        issue = self._with_retry(self._repo.get_issue, issue_number)
        self._with_retry(issue.add_to_labels, label)

    def existing_pr_for_webhook(self, issue_number: int) -> Optional[PR]:
        """Hot path: O(1), state=open, parses branch name (immutable).
        Used for cross-run_id idempotency before enqueue."""
        for pr in self.list_open_pulls():
            if pr.head and pr.head.ref and f"issue_{issue_number}" in pr.head.ref:
                return pr
        return None

    def _existing_pr_for_recovery(self, issue_number: int) -> Optional[PR]:
        """Recovery path (queue.db corrupt). Pagination tolerated."""
        page = 1
        while page <= 3:
            for pr in self._repo.get_pulls(state="all", per_page=100, page=page):
                if pr.head.ref and f"issue_{issue_number}" in pr.head.ref:
                    return PR(number=pr.number, title=pr.title, body=pr.body or "",
                              head=pr.head, state=pr.state)
            page += 1
        return None
```

### 2. Create `src/orchestrator/integrations/__init__.py`

```python
# Package marker
```

### 3. Create `src/orchestrator/integrations/webhook.py`

```python
"""GitHub webhook handler for issue_comment and issues.opened events."""

import json
import os
from typing import Optional

from orchestrator.clients.github import GitHubClient
from orchestrator.storage.work_queue import enqueue_issue, init_queue_db


PATCHFORGE_LABELS = {
    "patchforge/pending": "Awaiting processing",
    "patchforge/processing": "Currently being processed",
    "patchforge/ready": "PR ready for review",
    "patchforge/failed": "Pipeline execution failed",
}


def handle_issue_opened(event: dict, gh: GitHubClient, queue_db_path: str) -> dict:
    """Handle issues.opened webhook event."""
    issue = event.get("issue", {})
    repo = event.get("repository", {})
    issue_number = issue.get("number")
    repo_full_name = repo.get("full_name")

    # Cross-run_id idempotency: check if a PR already exists for this issue
    existing = gh.existing_pr_for_webhook(issue_number)
    if existing:
        return {"status": "skipped", "reason": "PR already exists", "pr_number": existing.number}

    # Enqueue
    conn = init_queue_db(queue_db_path)
    run_id = enqueue_issue(
        conn,
        issue_number=issue_number,
        repo=repo_full_name,
        payload=json.dumps({
            "title": issue.get("title", ""),
            "body": issue.get("body", ""),
            "labels": [l["name"] for l in issue.get("labels", [])],
            "repo_url": repo.get("clone_url"),
        })
    )

    if run_id:
        # Labels are cosmetic — update asynchronously after SQLite commit
        try:
            gh.add_label(issue_number, "patchforge/pending")
        except Exception:
            pass  # Label failure never blocks pipeline execution
        return {"status": "enqueued", "run_id": run_id}
    else:
        return {"status": "duplicate", "reason": "issue_lock integrity error"}
```

### 4. PR body assembly

```python
def assemble_pr_body(run_dir: Path, store_base_url: str, goal: str, run_id: str) -> str:
    body = f"""
## PatchForge: {goal}

**Run ID:** {run_id}

### Artifacts
- [patch.diff]({store_base_url}/{run_id}/patch.diff)
- [validation.json]({store_base_url}/{run_id}/validation.json)
- [apply.json]({store_base_url}/{run_id}/apply.json)
- [risk_gate.json]({store_base_url}/{run_id}/risk_gate.json)
"""
    if len(body.encode("utf-8")) > 120_000:
        body = body[:100_000] + "\n\n*[Body truncated — see artifact links for full data]*"
    return body
```

---

### 5. Add PyGithub dependency

`pyproject.toml` — add to `[project.dependencies]`:
```toml
"PyGithub>=2.0",
```

---

## Files to Create/Modify

- `pyproject.toml` — Add PyGithub dependency
- **NEW** `src/orchestrator/clients/github.py` — GitHubClient with rate-limit retry
- **NEW** `src/orchestrator/integrations/__init__.py` — Package marker
- **NEW** `src/orchestrator/integrations/webhook.py` — Webhook handler

---

## Acceptance Criteria

- [ ] `patchforge plan --issue 42` opens a PR with patch.diff, validation.json, verdict
- [ ] Same issue processed twice (intra-run_id) → `get_pr_for_branch` finds existing branch → skips
- [ ] Same issue after `queue.db` recovery (cross-run_id) → `_existing_pr_for_webhook` finds PR via `issue_N` in branch name → discards webhook
- [ ] Branch has exactly 1 commit: `"PatchForge: {run_id} [skip ci]"`
- [ ] Labels are display-only: if label update fails, pipeline execution is unaffected
- [ ] PR body respects GitHub API size limits (truncation with warning)
- [ ] Rate limit backoff with jitter prevents secondary rate limiting

---

## Test skeleton (create before running pytest)

Create `tests/test_github.py` with these cases:
```python
# Note: Use @patch('orchestrator.clients.github.Github') to mock all HTTP calls. Never call GitHub API in unit tests.

def test_create_pr_rate_limit_retry():
    """Verify _with_retry backoffs on 403 Rate Limit exception."""
    pass

def test_existing_pr_for_webhook_idempotency():
    """Verify webhook handler ignores duplicate events if PR branch exists."""
    pass
```

## Verification

```bash
# Requires GitHub token; run against a test repo
PATCHFORGE_GITHUB_TOKEN=ghp_xxx pytest tests/ -k test_github -v

# Manual: test PR creation
python -c "
from orchestrator.clients.github import GitHubClient
gh = GitHubClient(token='ghp_xxx', repo='owner/test-repo')
pr = gh.create_pr(title='Test PR', body='Test body', head='patchforge/run_test/issue_1')
print(f'PR #{pr.number} created')
pr2 = gh.get_pr_for_branch('patchforge/run_test/issue_1')
assert pr2 is not None
print('Idempotency check OK')
"
```

## Rollback

```bash
git rm -r src/orchestrator/clients/github.py src/orchestrator/integrations/
```
