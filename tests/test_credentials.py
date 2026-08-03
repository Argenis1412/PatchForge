from __future__ import annotations

import os
from pathlib import Path

import pytest

from orchestrator.clients.credentials import (
    PROVIDER_ENV_VARS,
    CredentialEligibility,
    CredentialResolutionError,
    resolve_operator_credentials,
)
from orchestrator.doctor import check_api_keys


def _environment(**values: str) -> dict[str, str]:
    return {**dict.fromkeys(PROVIDER_ENV_VARS.values(), ""), **values}


def test_inherited_environment_is_the_complete_source(tmp_path: Path):
    context = resolve_operator_credentials(
        target_path=tmp_path,
        inherited_environment=_environment(ANTHROPIC_API_KEY="anthropic-key"),
    )

    assert context.source == "inherited_environment"
    assert context.is_eligible("claude")
    assert not context.is_eligible("gemini")


def test_explicit_file_replaces_inherited_environment(tmp_path: Path):
    env_file = tmp_path / "operator.env"
    env_file.write_text("GOOGLE_API_KEY=google-key\n", encoding="utf-8")

    context = resolve_operator_credentials(
        target_path=tmp_path / "target",
        env_file=env_file,
        inherited_environment=_environment(ANTHROPIC_API_KEY="inherited-key"),
    )

    assert not context.is_eligible("claude")
    assert context.is_eligible("gemini")


@pytest.mark.parametrize("value", [None, "", " key", "key ", "key\n", "key\x00value"])
def test_static_invalid_credentials_are_ineligible(tmp_path: Path, value: str | None):
    environment = _environment()
    if value is not None:
        environment["OPENROUTER_API_KEY"] = value

    context = resolve_operator_credentials(target_path=tmp_path, inherited_environment=environment)

    assert context.eligibility["openrouter"] is CredentialEligibility.INELIGIBLE


def test_explicit_file_inside_target_is_rejected_without_path(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    env_file = target / "operator.env"
    env_file.write_text("ANTHROPIC_API_KEY=key\n", encoding="utf-8")

    with pytest.raises(CredentialResolutionError) as error:
        resolve_operator_credentials(target_path=target, env_file=env_file)

    assert str(env_file) not in str(error.value)


def test_symlink_to_target_file_is_rejected_without_path(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    target_file = target / "operator.env"
    target_file.write_text("ANTHROPIC_API_KEY=key\n", encoding="utf-8")
    link = tmp_path / "operator-link.env"
    try:
        link.symlink_to(target_file)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(CredentialResolutionError) as error:
        resolve_operator_credentials(target_path=target, env_file=link)

    assert str(target_file) not in str(error.value)


def test_implicit_dotenv_files_do_not_change_resolved_credentials(tmp_path: Path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=cwd-key\n", encoding="utf-8")
    (target / ".env").write_text("OPENROUTER_API_KEY=target-key\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    context = resolve_operator_credentials(
        target_path=target,
        inherited_environment=_environment(ANTHROPIC_API_KEY="inherited-key"),
    )

    assert context.is_eligible("claude")
    assert not context.is_eligible("gemini")
    assert not context.is_eligible("openrouter")


def test_context_repr_and_doctor_diagnostics_do_not_expose_secret(tmp_path: Path, monkeypatch):
    secret = "sensitive-operator-secret"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    context = resolve_operator_credentials(
        target_path=tmp_path,
        inherited_environment=_environment(ANTHROPIC_API_KEY=secret),
    )

    assert secret not in repr(context)
    assert all(secret not in result.message for result in check_api_keys(context))
    assert "ANTHROPIC_API_KEY" not in os.environ
