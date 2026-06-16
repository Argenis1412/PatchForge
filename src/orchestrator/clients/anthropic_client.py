"""Anthropic (Claude) API client singleton."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic

_client = None


def get_anthropic_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client
