"""OpenRouter API client factory using httpx."""

from __future__ import annotations

import httpx

from orchestrator.clients import TIMEOUT_SECONDS

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def create_openrouter_client(credential: str) -> httpx.Client:
    """Create a client for one explicitly resolved credential."""
    return httpx.Client(
        base_url=OPENROUTER_BASE_URL,
        headers={"Authorization": f"Bearer {credential}"},
        timeout=TIMEOUT_SECONDS,
    )
