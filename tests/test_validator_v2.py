from pathlib import Path

import pytest

from orchestrator.agents.validator import adapters
from orchestrator.agents.validator.adapters import run_v2_validators
from orchestrator.agents.validator.process import ProcessResult, execute_process, prepare_process
from orchestrator.schemas.config import ValidatorConfig, ValidatorRole
from orchestrator.schemas.git import ValidationWorkspace
from orchestrator.schemas.validator_output import CoverageStatus, ExecutionState, OverallStatus
from orchestrator.validation_workspace import write_validation_json


def _validator(identifier: str, adapter: str = "ruff", **kwargs) -> ValidatorConfig:
    return ValidatorConfig(id=identifier, adapter=adapter, **kwargs)


@pytest.mark.unit
def test_v2_results_keep_declaration_identity_and_order(monkeypatch, tmp_path):
    raw_results = iter([ProcessResult(return_code=0), ProcessResult(return_code=0)])
    monkeypatch.setattr(adapters, "_raw_result", lambda *_: next(raw_results))

    output = run_v2_validators(
        "run-1",
        tmp_path,
        [_validator("unit", "pytest"), _validator("integration", "pytest")],
        30,
    )

    assert output.result_profile == "v2"
    assert output.overall_status is OverallStatus.APPROVED
    assert [(tool.validator_id, tool.declaration_index) for tool in output.tools] == [
        ("unit", 0),
        ("integration", 1),
    ]


@pytest.mark.unit
def test_v2_success_codes_override_legacy_pytest_empty_collection(monkeypatch, tmp_path):
    monkeypatch.setattr(adapters, "_raw_result", lambda *_: ProcessResult(return_code=5))

    output = run_v2_validators("run-2", tmp_path, [_validator("tests", "pytest")], 30)

    assert output.overall_status is OverallStatus.FAILED
    assert output.overall_passed is False
    assert output.tools[0].status is ExecutionState.FAILED


@pytest.mark.unit
def test_v2_command_override_has_declared_only_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(adapters, "_raw_result", lambda *_: ProcessResult(return_code=0))
    validator = _validator("lint", "ruff", command=["ruff", "--version"])

    output = run_v2_validators("run-3", tmp_path, [validator], 30)

    assert output.tools[0].role_coverage == {"lint": CoverageStatus.DECLARED_ONLY}


@pytest.mark.unit
def test_v2_tsc_override_runs_without_frontend(monkeypatch, tmp_path):
    captured = []

    def fake_execute(prepared, timeout):
        captured.append(prepared.argv)
        return ProcessResult(return_code=0)

    monkeypatch.setattr(adapters, "execute_process", fake_execute)
    validator = _validator("types", "tsc", command=["custom-tsc", "--noEmit"])

    output = run_v2_validators("run-3b", tmp_path, [validator], 30)

    assert output.overall_status is OverallStatus.APPROVED
    assert captured == [("custom-tsc", "--noEmit")]


@pytest.mark.unit
def test_v2_unavailable_is_incomplete_and_stops_remaining_validators(monkeypatch, tmp_path):
    monkeypatch.setattr(
        adapters, "_raw_result", lambda *_: ProcessResult(return_code=None, unavailable=True)
    )

    output = run_v2_validators(
        "run-4", tmp_path, [_validator("types", "mypy"), _validator("lint", "ruff")], 30
    )

    assert output.overall_status is OverallStatus.INCOMPLETE
    assert output.overall_passed is False
    assert [tool.status for tool in output.tools] == [
        ExecutionState.UNAVAILABLE,
        ExecutionState.NOT_RUN,
    ]
    assert output.tools[1].role_coverage == {"lint": CoverageStatus.ABSENT}


@pytest.mark.unit
def test_v2_cleanup_failure_is_incomplete(monkeypatch, tmp_path):
    monkeypatch.setattr(
        adapters,
        "_raw_result",
        lambda *_: ProcessResult(return_code=None, timed_out=True, cleanup_failed=True),
    )

    output = run_v2_validators("run-5", tmp_path, [_validator("lint")], 30)

    assert output.tools[0].status is ExecutionState.CLEANUP_FAILED
    assert output.overall_status is OverallStatus.INCOMPLETE


@pytest.mark.unit
def test_v2_failed_execution_remains_failed_when_later_declarations_are_not_run(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(adapters, "_raw_result", lambda *_: ProcessResult(return_code=1))

    output = run_v2_validators(
        "run-5b", tmp_path, [_validator("lint"), _validator("tests", "pytest")], 30
    )

    assert [tool.status for tool in output.tools] == [
        ExecutionState.FAILED,
        ExecutionState.NOT_RUN,
    ]
    assert output.overall_status is OverallStatus.FAILED


@pytest.mark.unit
def test_v2_timeout_remains_failed_when_later_declarations_are_not_run(monkeypatch, tmp_path):
    monkeypatch.setattr(
        adapters,
        "_raw_result",
        lambda *_: ProcessResult(return_code=None, timed_out=True),
    )

    output = run_v2_validators(
        "run-5c", tmp_path, [_validator("lint"), _validator("tests", "pytest")], 30
    )

    assert [tool.status for tool in output.tools] == [
        ExecutionState.TIMEOUT,
        ExecutionState.NOT_RUN,
    ]
    assert output.overall_status is OverallStatus.FAILED


@pytest.mark.unit
def test_historical_v1_output_remains_readable_without_profile():
    from orchestrator.schemas.validator_output import ValidatorOutput

    output = ValidatorOutput.model_validate({"overall_passed": True, "tools": []})

    assert output.result_profile is None
    assert output.overall_status is None


@pytest.mark.unit
def test_empty_v2_execution_is_incomplete(tmp_path):
    output = run_v2_validators("run-empty", tmp_path, [], 30)

    assert output.overall_status is OverallStatus.INCOMPLETE
    assert output.overall_passed is False


@pytest.mark.unit
def test_v2_output_requires_overall_status_and_tool_metadata():
    from pydantic import ValidationError

    from orchestrator.schemas.validator_output import ValidatorOutput

    with pytest.raises(ValidationError, match="overall_status"):
        ValidatorOutput.model_validate({"overall_passed": True, "result_profile": "v2"})

    with pytest.raises(ValidationError, match="role_coverage"):
        ValidatorOutput.model_validate(
            {
                "overall_passed": True,
                "overall_status": "approved",
                "result_profile": "v2",
                "tools": [
                    {
                        "tool": "ruff",
                        "adapter": "ruff",
                        "passed": True,
                        "return_code": 0,
                        "validator_id": "lint",
                        "declaration_index": 0,
                        "status": "approved",
                        "declared_roles": ["lint"],
                    }
                ],
            }
        )

    with pytest.raises(ValidationError, match="declared_roles"):
        ValidatorOutput.model_validate(
            {
                "overall_passed": True,
                "overall_status": "approved",
                "result_profile": "v2",
                "tools": [
                    {
                        "tool": "ruff",
                        "adapter": "ruff",
                        "passed": True,
                        "return_code": 0,
                        "validator_id": "lint",
                        "declaration_index": 0,
                        "status": "approved",
                        "role_coverage": {"lint": "verified"},
                    }
                ],
            }
        )


@pytest.mark.unit
def test_command_adapter_keeps_declared_roles(monkeypatch, tmp_path):
    monkeypatch.setattr(adapters, "_raw_result", lambda *_: ProcessResult(return_code=0))
    validator = _validator(
        "checks",
        "command",
        command=["custom-check"],
        roles=[ValidatorRole.TEST],
    )

    output = run_v2_validators("run-6", tmp_path, [validator], 30)

    assert output.tools[0].role_coverage == {"test": CoverageStatus.DECLARED_ONLY}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("adapter", "roles", "expected_command"),
    [
        ("flake8", None, ["flake8", "."]),
        ("mypy", None, ["mypy", "."]),
        ("pylint", None, ["pylint", "."]),
        ("unittest", None, ["-m", "unittest", "discover"]),
        ("tox", [ValidatorRole.TEST], ["tox"]),
    ],
)
def test_v2_builtin_adapters_use_standard_commands(
    monkeypatch, tmp_path, adapter, roles, expected_command
):
    captured = []

    def fake_execute(prepared, timeout):
        captured.append(list(prepared.argv))
        return ProcessResult(return_code=0)

    monkeypatch.setattr(adapters, "execute_process", fake_execute)
    validator = _validator("check", adapter, roles=roles)

    output = run_v2_validators("run-7", tmp_path, [validator], 30)

    assert output.overall_status is OverallStatus.APPROVED
    assert captured[0][-len(expected_command) :] == expected_command


@pytest.mark.unit
def test_validation_workspace_writes_v2_result_atomically(tmp_path):
    workspace = ValidationWorkspace(
        original_root=tmp_path,
        temporary_root=tmp_path,
        patch_path=tmp_path / "patch.diff",
    )
    output = run_v2_validators("run-8", tmp_path, [], 30)

    path = write_validation_json(workspace, output)

    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()


@pytest.mark.unit
def test_validation_workspace_preserves_original_file_when_replace_fails(monkeypatch, tmp_path):
    workspace = ValidationWorkspace(
        original_root=tmp_path,
        temporary_root=tmp_path,
        patch_path=tmp_path / "patch.diff",
    )
    destination = tmp_path / "validation.json"
    destination.write_text("original", encoding="utf-8")
    temporary = destination.with_suffix(".json.tmp")
    original_replace = Path.replace

    def fail_replace(path, target):
        if path == temporary:
            raise OSError("replace failed")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_validation_json(workspace, run_v2_validators("run-9", tmp_path, [], 30))

    assert destination.read_text(encoding="utf-8") == "original"
    assert not temporary.exists()


@pytest.mark.unit
def test_process_spawn_oserror_is_unavailable(monkeypatch, tmp_path):
    import orchestrator.agents.validator.process as process_module

    monkeypatch.setattr(
        process_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )

    result = execute_process(prepare_process(["tool"], tmp_path), 30)

    assert result.unavailable is True
    assert result.return_code is None
    assert "denied" in result.stderr
