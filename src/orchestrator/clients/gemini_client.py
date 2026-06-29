"""Google Gemini API client singleton."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from orchestrator.clients import TIMEOUT_SECONDS

if TYPE_CHECKING:
    from google import genai

_client = None


def get_gemini_client() -> genai.Client:
    global _client
    if _client is None:
        from google import genai
        from google.genai import types

        _client = genai.Client(
            api_key=os.getenv("GOOGLE_API_KEY"),
            http_options=types.HttpOptions(timeout=TIMEOUT_SECONDS * 1000),
        )
    return _client
