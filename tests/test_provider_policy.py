from __future__ import annotations

import pytest

from orchestrator.clients.credentials import resolve_operator_credentials
from orchestrator.provider_policy import (
    PROVIDERS,
    InvalidForceProviderError,
    ProviderDefinition,
    effective_provider_chain,
    eligible_providers,
    evaluate_credential_eligibility,
    evaluate_provider_policy,
    provider_chain,
    validate_force_provider_name,
)


@pytest.mark.parametrize(
    ("stage", "risk_level", "expected"),
    [
        ("architect", None, ("claude", "gemini", "openrouter")),
        ("executor", "low", ("gemini", "openrouter", "claude")),
        ("executor", "medium", ("openrouter", "gemini", "claude")),
        ("executor", "high", ("claude",)),
        ("scout", None, ("gemini", "openrouter", "claude")),
        ("validator_summary", None, ("gemini", "openrouter", "claude")),
    ],
)
def test_provider_policy_matches_current_declared_chains(stage, risk_level, expected):
    assert provider_chain(stage, risk_level) == expected


def test_eligible_providers_preserves_policy_order(tmp_path):
    context = resolve_operator_credentials(
        target_path=tmp_path,
        inherited_environment={
            "ANTHROPIC_API_KEY": "claude-key",
            "GOOGLE_API_KEY": "gemini-key",
        },
    )

    assert eligible_providers(context, stage="executor", risk_level="low") == ("gemini", "claude")


def test_effective_provider_chain_applies_admissible_override():
    assert effective_provider_chain("executor", risk_level="medium", force_provider="claude") == (
        "claude",
    )


def test_effective_provider_chain_rejects_high_risk_non_claude_override():
    with pytest.raises(ValueError, match="not allowed"):
        effective_provider_chain("executor", risk_level="high", force_provider="gemini")


def test_eligible_providers_respects_forced_provider(tmp_path):
    context = resolve_operator_credentials(
        target_path=tmp_path,
        inherited_environment={
            "ANTHROPIC_API_KEY": "claude-key",
            "GOOGLE_API_KEY": "gemini-key",
        },
    )

    assert eligible_providers(
        context,
        stage="architect",
        force_provider="gemini",
    ) == ("gemini",)


def test_provider_metadata_is_non_secret_and_complete():
    expected = {
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
    assert expected == PROVIDERS


def test_unknown_policy_route_is_rejected():
    with pytest.raises(ValueError, match="No provider policy"):
        provider_chain("unknown")


def test_public_force_provider_validation_uses_policy_catalog():
    for provider in PROVIDERS:
        validate_force_provider_name(provider)
    with pytest.raises(InvalidForceProviderError, match="Invalid value"):
        validate_force_provider_name("unknown")


def test_structured_policy_and_eligibility_match_compatible_facade(tmp_path):
    context = resolve_operator_credentials(
        target_path=tmp_path,
        inherited_environment={"GOOGLE_API_KEY": "gemini-key"},
    )
    policy = evaluate_provider_policy("architect", force_provider="gemini")
    eligibility = evaluate_credential_eligibility(context, policy.providers)

    assert policy.status == "admissible"
    assert eligibility.status == "eligible"
    assert eligible_providers(context, stage="architect", force_provider="gemini") == (
        eligibility.providers
    )


def test_structured_policy_rejects_known_inadmissible_force_provider():
    evaluation = evaluate_provider_policy("executor", risk_level="high", force_provider="gemini")

    assert evaluation.status == "rejected"
