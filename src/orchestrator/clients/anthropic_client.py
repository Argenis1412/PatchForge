"""Anthropic (Claude) API client factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.clients import TIMEOUT_SECONDS

if TYPE_CHECKING:
    import anthropic


def create_anthropic_client(credential: str) -> anthropic.Anthropic:
    """Create a client for one explicitly resolved credential."""
    import anthropic

    return anthropic.Anthropic(api_key=credential, timeout=TIMEOUT_SECONDS)
