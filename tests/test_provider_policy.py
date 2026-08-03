from __future__ import annotations

import pytest

from orchestrator.clients.credentials import resolve_operator_credentials
from orchestrator.provider_policy import PROVIDERS, eligible_providers, provider_chain


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

    assert eligible_providers(context, stage="architect") == ("claude", "gemini")


def test_provider_metadata_is_non_secret_and_complete():
    assert {definition.credential_environment_variable for definition in PROVIDERS.values()} == {
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
    }


def test_unknown_policy_route_is_rejected():
    with pytest.raises(ValueError, match="No provider policy"):
        provider_chain("unknown")
