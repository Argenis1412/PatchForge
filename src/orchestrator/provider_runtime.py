"""Invocation-scoped provider configuration and credentials."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

from orchestrator.clients.credentials import CredentialContext

if TYPE_CHECKING:
    from orchestrator.schemas.config import TargetConfig


# Keep model identifiers beside runtime defaults so cost classification and
# per-invocation model resolution share one source of truth.
MODEL_GEMINI = "gemini-2.5-flash"
MODEL_OPENROUTER = "openrouter/free"
MODEL_CLAUDE = "claude-sonnet-4-6"

_DEFAULT_MODELS: Mapping[str, str] = {
    "gemini": MODEL_GEMINI,
    "openrouter": MODEL_OPENROUTER,
    "claude": MODEL_CLAUDE,
}


@dataclass(frozen=True)
class ProviderRuntime:
    """Credentials and model choices that belong to one command invocation."""

    credentials: CredentialContext
    models: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "models", MappingProxyType(dict(self.models)))

    @classmethod
    def from_config(
        cls, credentials: CredentialContext, config: TargetConfig | None
    ) -> ProviderRuntime:
        models = dict(_DEFAULT_MODELS)
        if config is not None and hasattr(config, "providers"):
            for provider, default in _DEFAULT_MODELS.items():
                provider_config = getattr(config.providers, provider, None)
                models[provider] = (
                    provider_config.model if provider_config and provider_config.model else default
                )
        return cls(credentials=credentials, models=models)

    def model_for(self, provider: str) -> str:
        return self.models.get(provider, _DEFAULT_MODELS.get(provider, provider))
