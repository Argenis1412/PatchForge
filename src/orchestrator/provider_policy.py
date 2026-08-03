"""Pure, shared provider policy for credential routing."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from orchestrator.clients.credentials import CredentialContext


@dataclass(frozen=True)
class ProviderDefinition:
    """Public, non-secret provider metadata."""

    name: str
    credential_environment_variable: str
    display_name: str
    doctor_check_name: str


PROVIDERS: Mapping[str, ProviderDefinition] = MappingProxyType(
    {
        "claude": ProviderDefinition(
            name="claude",
            credential_environment_variable="ANTHROPIC_API_KEY",
            display_name="Claude",
            doctor_check_name="anthropic_api_key",
        ),
        "gemini": ProviderDefinition(
            name="gemini",
            credential_environment_variable="GOOGLE_API_KEY",
            display_name="Gemini",
            doctor_check_name="google_api_key",
        ),
        "openrouter": ProviderDefinition(
            name="openrouter",
            credential_environment_variable="OPENROUTER_API_KEY",
            display_name="OpenRouter",
            doctor_check_name="openrouter_api_key",
        ),
    }
)

PROVIDER_ENV_VARS: Mapping[str, str] = MappingProxyType(
    {name: definition.credential_environment_variable for name, definition in PROVIDERS.items()}
)

_CHAINS: Mapping[tuple[str, str | None], tuple[str, ...]] = MappingProxyType(
    {
        ("architect", None): ("claude", "gemini", "openrouter"),
        ("executor", "low"): ("gemini", "openrouter", "claude"),
        ("executor", "medium"): ("openrouter", "gemini", "claude"),
        ("executor", "high"): ("claude",),
        ("scout", None): ("gemini", "openrouter", "claude"),
        ("validator_summary", None): ("gemini", "openrouter", "claude"),
    }
)


def provider_chain(stage: str, risk_level: str | None = None) -> tuple[str, ...]:
    """Return the current declared provider order for one stage."""
    try:
        return _CHAINS[(stage, risk_level)]
    except KeyError as exc:
        raise ValueError(f"No provider policy is declared for {stage!r}/{risk_level!r}") from exc


def eligible_providers(
    credential_context: CredentialContext,
    *,
    stage: str,
    risk_level: str | None = None,
) -> tuple[str, ...]:
    """Return static credential-eligible providers in declared order."""
    return tuple(
        provider
        for provider in provider_chain(stage, risk_level)
        if credential_context.is_eligible(provider)
    )
