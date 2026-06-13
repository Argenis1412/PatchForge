"""Issue markdown schema for --issue-file.

Parses frontmatter + body from a human-written issue markdown file.
Frontmatter uses a naive YAML-subset parser — no external dependencies.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IssueInput(BaseModel):
    title: str = Field(default="Untitled issue")
    severity: Literal["low", "medium", "high"] = "medium"
    labels: list[str] = []
    body: str
    raw: str


def parse_issue_markdown(content: str) -> IssueInput:
    """Parse frontmatter + body from a markdown issue file.

    Format:
    ```
    ---
    title: Fix connection pooling
    severity: high
    labels: bug, performance
    ---
    Body text here...
    ```

    Frontmatter is delimited by ``---`` lines at the start of the file.
    Empty lines inside the frontmatter block are ignored.
    If no frontmatter is found the entire content is treated as body
    and defaults are used for title/severity/labels.

    Raises ``ValueError`` if *content* is empty or frontmatter is
    structurally malformed.
    """
    if not content or not content.strip():
        raise ValueError("Issue file is empty")

    raw = content
    title = "Untitled issue"
    severity: Literal["low", "medium", "high"] = "medium"
    labels: list[str] = []
    body = content

    if content.startswith("---"):
        # Find the closing --- delimiter (line-aware, avoid mid-line match)
        rest_lines = content[3:].splitlines(keepends=True)
        closing_idx = next(
            (i for i, line in enumerate(rest_lines) if line.strip() == "---"),
            None,
        )
        if closing_idx is None:
            # No closing delimiter — treat everything as body
            body = content
        else:
            fm_block = "".join(rest_lines[:closing_idx])
            body = "".join(rest_lines[closing_idx + 1 :])

            for line in fm_block.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if ":" not in stripped:
                    raise ValueError(f"Invalid frontmatter at line: {stripped!r}")
                key, _, val = stripped.partition(":")
                k = key.strip()
                v = val.strip()
                if not k:
                    raise ValueError(f"Invalid frontmatter at line: {stripped!r}")
                if k == "title":
                    title = v
                elif k == "severity":
                    if v not in ("low", "medium", "high"):
                        raise ValueError(f"Invalid severity {v!r}; expected low, medium, or high")
                    severity = v  # type: ignore
                elif k == "labels":
                    if v:
                        labels = [x.strip() for x in v.split(",") if x.strip()]
                # Unknown keys are silently ignored

    return IssueInput(
        title=title,
        severity=severity,
        labels=labels,
        body=body.strip(),
        raw=raw,
    )
