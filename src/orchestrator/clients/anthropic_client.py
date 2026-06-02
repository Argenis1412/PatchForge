import os

_client = None


def get_anthropic_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client
