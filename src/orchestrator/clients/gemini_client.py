"""Google Gemini API client factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.clients import TIMEOUT_SECONDS

if TYPE_CHECKING:
    from google import genai


def create_gemini_client(credential: str) -> genai.Client:
    """Create a client for one explicitly resolved credential."""
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=credential,
        http_options=types.HttpOptions(timeout=TIMEOUT_SECONDS * 1000),
    )
