"""Phase 3 authorization policy tests."""

from pathlib import Path

import pytest

from orchestrator.agents.validator import adapters
from orchestrator.agents.validator.adapters import run_v2_validators
from orchestrator.agents.validator.process import ProcessResult
from orchestrator.schemas.config import TargetConfig, ValidatorConfig
from orchestrator.schemas.validator_output import DecisionReason, ValidatorOutput
from orchestrator.validation_decision import (
    attach_validation_decision,
    bind_validation_subject,
    evaluate_validation,
)


def _config(tmp_path: Path, validators: list[ValidatorConfig] | None) -> TargetConfig:
    data = {
        "target_path": tmp_path,
        "workspace_path": tmp_path.parent / f"{tmp_path.name}-workspace",
        "validators": validators,
    }
    if validators is not None:
        data["schema_version"] = "2.0"
    return TargetConfig(**data)


@pytest.mark.unit
def test_historical_validation_artifact_is_readable_but_not_authorizable():
    historical = ValidatorOutput.model_validate({"overall_passed": True, "tools": []})

    decision = evaluate_validation(historical)

    assert decision.authorized is False
    assert decision.reasons == [DecisionReason.UNSUPPORTED_ARTIFACT]


@pytest.mark.unit
def test_fresh_legacy_result_remains_authorizable_during_transition():
    decision = evaluate_validation(ValidatorOutput(overall_passed=True), fresh=True)

    assert decision.authorized is True


@pytest.mark.unit
def test_new_v1_execution_keeps_transitional_overall_passed_semantics(tmp_path):
    output = attach_validation_decision(
        ValidatorOutput(overall_passed=True), _config(tmp_path, None)
    )

    assert output.schema_version == 2
    assert output.authorization_profile.value == "legacy_v1_compat@1"
    assert output.decision is not None and output.decision.authorized is True


@pytest.mark.unit
def test_v2_requires_verified_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(adapters, "_raw_result", lambda *_: ProcessResult(return_code=0))
    config = _config(
        tmp_path,
        [ValidatorConfig(id="lint", adapter="ruff", command=["ruff", "check", "."])],
    )
    output = attach_validation_decision(
        run_v2_validators("run-v2", tmp_path, config.validators or [], 30), config
    )

    assert output.decision is not None and output.decision.authorized is False
    assert output.decision.reasons == [DecisionReason.ROLE_NOT_VERIFIED]


@pytest.mark.unit
def test_v2_standard_adapter_with_verified_coverage_authorizes(monkeypatch, tmp_path):
    monkeypatch.setattr(adapters, "_raw_result", lambda *_: ProcessResult(return_code=0))
    config = _config(tmp_path, [ValidatorConfig(id="lint", adapter="ruff")])
    output = attach_validation_decision(
        run_v2_validators("run-v2", tmp_path, config.validators or [], 30), config
    )

    assert output.decision is not None and output.decision.authorized is True
    assert output.decision.reasons == [DecisionReason.APPROVED]


@pytest.mark.unit
def test_requirements_digest_changes_with_validator_command(tmp_path):
    first = attach_validation_decision(
        ValidatorOutput(overall_passed=True),
        _config(tmp_path, [ValidatorConfig(id="lint", adapter="ruff")]),
    )
    second = attach_validation_decision(
        ValidatorOutput(overall_passed=True),
        _config(
            tmp_path,
            [ValidatorConfig(id="lint", adapter="ruff", command=["ruff", "check", "."])],
        ),
    )

    assert first.validation_requirements.digest != second.validation_requirements.digest


@pytest.mark.unit
def test_subject_mismatch_denies_a_fresh_v2_result(monkeypatch, tmp_path):
    monkeypatch.setattr(adapters, "_raw_result", lambda *_: ProcessResult(return_code=0))
    config = _config(tmp_path, [ValidatorConfig(id="lint", adapter="ruff")])
    output = bind_validation_subject(
        attach_validation_decision(
            run_v2_validators("run-v2", tmp_path, config.validators or [], 30), config
        ),
        base_commit="a" * 40,
        patch_checksum="b" * 64,
    )

    assert (
        evaluate_validation(output, expected_subject=output.validation_subject).authorized is True
    )
    assert (
        evaluate_validation(
            output,
            expected_subject=output.validation_subject.model_copy(
                update={"patch_checksum": "c" * 64}
            ),
        ).authorized
        is False
    )
