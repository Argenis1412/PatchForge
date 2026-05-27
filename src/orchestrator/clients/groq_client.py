import os

import httpx

_client = None

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

def get_groq_client():
    global _client
    if _client is None:
        _client = httpx.Client(
            base_url=GROQ_BASE_URL,
            headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
            timeout=30.0,
        )
    return _client
