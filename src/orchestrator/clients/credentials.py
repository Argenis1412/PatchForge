"""Resolve operator-supplied LLM credentials without mutating process state."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from dotenv import dotenv_values

PROVIDER_ENV_VARS: Mapping[str, str] = MappingProxyType(
    {
        "claude": "ANTHROPIC_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
)


class CredentialEligibility(StrEnum):
    """Static credential eligibility, without provider or network calls."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"


class CredentialResolutionError(ValueError):
    """Raised when an explicit credential file violates the trust boundary."""


@dataclass(frozen=True)
class CredentialContext:
    """Immutable, invocation-scoped provider credentials.

    The credential mapping is intentionally private and omitted from ``repr``.
    Callers may inspect eligibility but only provider-client factories should ask
    for the corresponding credential value.
    """

    source: str
    eligibility: Mapping[str, CredentialEligibility]
    _credentials: Mapping[str, str] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "eligibility", MappingProxyType(dict(self.eligibility)))
        object.__setattr__(self, "_credentials", MappingProxyType(dict(self._credentials)))

    def is_eligible(self, provider: str) -> bool:
        return self.eligibility.get(provider) is CredentialEligibility.ELIGIBLE

    def credential_for(self, provider: str) -> str | None:
        """Return a valid credential for an internal provider factory."""
        return self._credentials.get(provider)


def _is_valid_credential(value: str | None) -> bool:
    if not value or value != value.strip():
        return False
    return not any(ord(character) < 32 or ord(character) == 127 for character in value)


def _resolve_explicit_file(env_file: Path, target_path: Path) -> Mapping[str, str | None]:
    resolved_file = env_file.resolve()
    resolved_target = target_path.resolve()
    if resolved_file.is_relative_to(resolved_target):
        raise CredentialResolutionError("Explicit credential file is not trusted for this target.")
    if not resolved_file.is_file():
        raise CredentialResolutionError("Explicit credential file is unavailable.")
    try:
        return dotenv_values(resolved_file)
    except OSError as exc:
        raise CredentialResolutionError("Explicit credential file is unavailable.") from exc


def resolve_operator_credentials(
    *,
    target_path: Path,
    env_file: Path | None = None,
    inherited_environment: Mapping[str, str] | None = None,
) -> CredentialContext:
    """Resolve one trusted source of provider credentials for an invocation."""
    if env_file is None:
        values: Mapping[str, str | None] = dict(
            os.environ if inherited_environment is None else inherited_environment
        )
        source = "inherited_environment"
    else:
        values = _resolve_explicit_file(env_file, target_path)
        source = "explicit_env_file"

    credentials: dict[str, str] = {}
    eligibility: dict[str, CredentialEligibility] = {}
    for provider, variable in PROVIDER_ENV_VARS.items():
        value = values.get(variable)
        if _is_valid_credential(value):
            credentials[provider] = value
            eligibility[provider] = CredentialEligibility.ELIGIBLE
        else:
            eligibility[provider] = CredentialEligibility.INELIGIBLE
    return CredentialContext(source=source, eligibility=eligibility, _credentials=credentials)
