"""Anthropic (Claude) API client singleton."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from orchestrator.clients import TIMEOUT_SECONDS

if TYPE_CHECKING:
    import anthropic

_client = None


def get_anthropic_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            timeout=TIMEOUT_SECONDS,
        )
    return _client
