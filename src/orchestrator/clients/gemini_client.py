import os

_client = None


def get_gemini_client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    return _client
