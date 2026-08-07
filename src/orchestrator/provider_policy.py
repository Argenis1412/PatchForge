"""Pure, shared provider policy for credential routing."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Mapping

if TYPE_CHECKING:
    from orchestrator.clients.credentials import CredentialContext


@dataclass(frozen=True)
class ProviderDefinition:
    """Public, non-secret provider metadata."""

    name: str
    credential_environment_variable: str
    display_name: str
    doctor_check_name: str


class InvalidForceProviderError(ValueError):
    """Raised when a public force-provider argument names an unknown provider."""


@dataclass(frozen=True)
class ProviderPolicyEvaluation:
    """Static policy evaluation separated from credential eligibility."""

    status: Literal["admissible", "rejected", "unavailable"]
    providers: tuple[str, ...] = ()
    message: str | None = None


@dataclass(frozen=True)
class CredentialEligibilityEvaluation:
    """Static credential evaluation for an already-admissible provider chain."""

    status: Literal["eligible", "evaluation_failed"]
    providers: tuple[str, ...] = ()


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


def validate_force_provider_name(force_provider: str | None) -> None:
    """Validate a public provider name against the policy-owned catalog."""
    if force_provider is not None and force_provider not in PROVIDERS:
        raise InvalidForceProviderError(
            f"Invalid value for --force-provider. Valid options are: {', '.join(PROVIDERS)}."
        )


def evaluate_provider_policy(
    stage: str,
    *,
    risk_level: str | None = None,
    force_provider: str | None = None,
) -> ProviderPolicyEvaluation:
    """Evaluate static routing policy without inspecting credentials."""
    validate_force_provider_name(force_provider)
    try:
        declared_chain = provider_chain(stage, risk_level)
    except Exception:
        return ProviderPolicyEvaluation(status="unavailable")
    if force_provider is not None and force_provider not in declared_chain:
        return ProviderPolicyEvaluation(
            status="rejected",
            message=f"Provider {force_provider!r} is not allowed for {stage!r}/{risk_level!r}",
        )
    return ProviderPolicyEvaluation(
        status="admissible", providers=(force_provider,) if force_provider else declared_chain
    )


def evaluate_credential_eligibility(
    credential_context: CredentialContext,
    providers: tuple[str, ...],
) -> CredentialEligibilityEvaluation:
    """Evaluate credentials only after policy has supplied an admissible chain."""
    try:
        eligible = tuple(
            provider for provider in providers if credential_context.is_eligible(provider)
        )
    except Exception:
        return CredentialEligibilityEvaluation(status="evaluation_failed")
    return CredentialEligibilityEvaluation(status="eligible", providers=eligible)


def _require_admissible(
    evaluation: ProviderPolicyEvaluation,
    *,
    stage: str,
    risk_level: str | None,
) -> tuple[str, ...]:
    """Return an admissible chain or preserve the compatible error contract."""
    if evaluation.status == "admissible":
        return evaluation.providers
    if evaluation.message is not None:
        raise ValueError(evaluation.message)
    raise ValueError(f"No provider policy is declared for {stage!r}/{risk_level!r}")


def effective_provider_chain(
    stage: str,
    *,
    risk_level: str | None = None,
    force_provider: str | None = None,
) -> tuple[str, ...]:
    """Return the policy-admissible chain after applying an optional override."""
    evaluation = evaluate_provider_policy(
        stage,
        risk_level=risk_level,
        force_provider=force_provider,
    )
    return _require_admissible(evaluation, stage=stage, risk_level=risk_level)


def eligible_providers(
    credential_context: CredentialContext,
    *,
    stage: str,
    risk_level: str | None = None,
    force_provider: str | None = None,
) -> tuple[str, ...]:
    """Return static credential-eligible providers in declared order."""
    policy = evaluate_provider_policy(
        stage,
        risk_level=risk_level,
        force_provider=force_provider,
    )
    providers = _require_admissible(policy, stage=stage, risk_level=risk_level)
    eligibility = evaluate_credential_eligibility(credential_context, providers)
    if eligibility.status == "evaluation_failed":
        raise ValueError("Provider credential eligibility evaluation failed")
    return eligibility.providers
