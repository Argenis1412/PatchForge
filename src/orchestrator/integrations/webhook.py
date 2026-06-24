"""GitHub webhook event handler for issue-based pipeline triggering."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from orchestrator.clients.github import GitHubClient
from orchestrator.storage import _sqlite_connect
from orchestrator.storage.work_queue import enqueue_issue

logger = logging.getLogger(__name__)

__all__ = ["handle_issue_opened"]

PATCHFORGE_LABELS = {
    "pending": "patchforge-pending",
    "processing": "patchforge-processing",
    "completed": "patchforge-completed",
    "failed": "patchforge-failed",
}


def handle_issue_opened(
    event: Dict[str, Any],
    gh: GitHubClient,
    queue_db_path: Path,
) -> Dict[str, Any]:
    issue_number = event["issue"]["number"]
    repo_name = event["repository"]["full_name"]

    existing = gh.existing_pr_for_webhook(issue_number)
    if existing is not None:
        return {
            "status": "skipped",
            "issue_number": issue_number,
            "pr_number": existing.number,
            "reason": "PR already exists",
        }

    conn = _sqlite_connect(queue_db_path)
    run_id = enqueue_issue(conn, issue_number, repo_name, json.dumps(event))
    conn.close()

    if run_id is None:
        return {
            "status": "duplicate",
            "issue_number": issue_number,
            "reason": "Already enqueued (lock active)",
        }

    try:
        gh.add_label(issue_number, PATCHFORGE_LABELS["pending"])
    except Exception:
        logger.warning("Failed to add label to issue #%d", issue_number, exc_info=True)

    return {
        "status": "enqueued",
        "issue_number": issue_number,
        "run_id": run_id,
    }
