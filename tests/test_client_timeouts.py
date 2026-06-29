"""Tests that each LLM client singleton wires TIMEOUT_SECONDS correctly."""

from unittest.mock import MagicMock, patch

from orchestrator.clients import TIMEOUT_SECONDS


class TestGeminiTimeout:
    def test_timeout_uses_milliseconds(self, monkeypatch):
        from google.genai import types

        from orchestrator.clients import gemini_client

        monkeypatch.setattr(gemini_client, "_client", None)

        mock_client_cls = MagicMock()
        monkeypatch.setattr("google.genai.Client", mock_client_cls)

        gemini_client.get_gemini_client()

        mock_client_cls.assert_called_once()
        call_kwargs = mock_client_cls.call_args[1]
        http_opts = call_kwargs["http_options"]
        assert isinstance(http_opts, types.HttpOptions)
        assert http_opts.timeout == TIMEOUT_SECONDS * 1000


class TestAnthropicTimeout:
    def test_timeout_seconds(self, monkeypatch):
        from orchestrator.clients import anthropic_client

        monkeypatch.setattr(anthropic_client, "_client", None)

        mock_cls = MagicMock()

        import sys

        fake_anthropic = MagicMock()
        fake_anthropic.Anthropic = mock_cls

        with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
            anthropic_client.get_anthropic_client()

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["timeout"] == TIMEOUT_SECONDS


class TestOpenRouterTimeout:
    def test_timeout_seconds(self, monkeypatch):
        from orchestrator.clients import openrouter_client

        monkeypatch.setattr(openrouter_client, "_client", None)

        mock_cls = MagicMock()
        monkeypatch.setattr(openrouter_client, "httpx", MagicMock(Client=mock_cls))

        openrouter_client.get_openrouter_client()

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["timeout"] == TIMEOUT_SECONDS


class TestTimeoutConstant:
    def test_value(self):
        assert TIMEOUT_SECONDS == 60

    def test_importable_from_clients_package(self):
        from orchestrator import clients

        assert hasattr(clients, "TIMEOUT_SECONDS")
        assert clients.TIMEOUT_SECONDS == 60
