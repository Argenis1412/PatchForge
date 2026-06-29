"""OpenRouter API client singleton using httpx."""

from __future__ import annotations

import os

import httpx

from orchestrator.clients import TIMEOUT_SECONDS

_client = None

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_openrouter_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            base_url=OPENROUTER_BASE_URL,
            headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"},
            timeout=TIMEOUT_SECONDS,
        )
    return _client
