"""OpenRouter API client singleton using httpx."""

from __future__ import annotations

import os

import httpx

_client = None

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_openrouter_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            base_url=OPENROUTER_BASE_URL,
            headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"},
            timeout=30.0,
        )
    return _client
