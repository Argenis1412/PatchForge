"""Tests for the issue markdown parser (--issue-file)."""

import pytest
from pydantic import ValidationError

from orchestrator.schemas.issue import IssueInput, parse_issue_markdown


class TestParseIssueMarkdown:
    def test_full_frontmatter(self):
        content = """---
title: Fix connection pooling
severity: high
labels: bug, performance
---
This is the body of the issue.
It can span multiple lines.
"""
        result = parse_issue_markdown(content)
        assert result.title == "Fix connection pooling"
        assert result.severity == "high"
        assert result.labels == ["bug", "performance"]
        assert "This is the body of the issue." in result.body
        assert result.raw == content

    def test_no_frontmatter(self):
        content = "Just a plain body with no frontmatter at all."
        result = parse_issue_markdown(content)
        assert result.title == "Untitled issue"
        assert result.severity == "medium"
        assert result.labels == []
        assert result.body == content

    def test_partial_frontmatter(self):
        content = """---
title: Only title
---
Body here."""
        result = parse_issue_markdown(content)
        assert result.title == "Only title"
        assert result.severity == "medium"
        assert result.labels == []
        assert "Body here." in result.body

    def test_comma_labels(self):
        content = """---
title: Bug fix
severity: low
labels: bug, performance, ui
---
body
"""
        result = parse_issue_markdown(content)
        assert result.labels == ["bug", "performance", "ui"]

    def test_title_with_colon(self):
        content = """---
title: Fix: broken on timeout — retry fails
---
body
"""
        result = parse_issue_markdown(content)
        assert result.title == "Fix: broken on timeout — retry fails"

    def test_empty_content_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_issue_markdown("")

    def test_malformed_frontmatter_raises(self):
        content = """---
: broken key
---
body
"""
        with pytest.raises(ValueError, match="Invalid frontmatter"):
            parse_issue_markdown(content)

    def test_raw_preserved(self):
        content = "no frontmatter here"
        result = parse_issue_markdown(content)
        assert result.raw == content

    def test_frontmatter_body_empty(self):
        content = """---
title: Just metadata
severity: high
---
"""
        result = parse_issue_markdown(content)
        assert result.title == "Just metadata"
        assert result.body == ""
        assert result.severity == "high"

    def test_labels_inconsistent_whitespace(self):
        content = """---
labels: bug ,  perf
---
body
"""
        result = parse_issue_markdown(content)
        assert result.labels == ["bug", "perf"]

    def test_blank_lines_in_frontmatter(self):
        content = """---
title: Foo

severity: high

labels: bug
---
body
"""
        result = parse_issue_markdown(content)
        assert result.title == "Foo"
        assert result.severity == "high"
        assert result.labels == ["bug"]

    def test_frontmatter_no_closing_delim(self):
        content = "---\ntitle: Not closed\nbody text"
        result = parse_issue_markdown(content)
        # No closing ---: treat everything as body
        assert result.title == "Untitled issue"
        assert result.body == content

    def test_only_severity(self):
        content = """---
severity: low
---
body
"""
        result = parse_issue_markdown(content)
        assert result.title == "Untitled issue"
        assert result.severity == "low"
        assert result.body == "body"


class TestIssueInputSchema:
    def test_defaults(self):
        inst = IssueInput(body="test", raw="test")
        assert inst.title == "Untitled issue"
        assert inst.severity == "medium"
        assert inst.labels == []

    def test_rejects_invalid_severity(self):
        with pytest.raises(ValidationError):
            IssueInput(body="test", raw="test", severity="critical")  # type: ignore
