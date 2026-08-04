from pathlib import Path

import pytest

from orchestrator.agents import validator as validator_agent
from orchestrator.agents.validator import adapters
from orchestrator.agents.validator.adapters import run_v2_validators
from orchestrator.agents.validator.process import ProcessResult, execute_process, prepare_process
from orchestrator.schemas.config import TargetConfig, ValidatorConfig, ValidatorRole
from orchestrator.schemas.git import ValidationWorkspace
from orchestrator.schemas.validator_output import CoverageStatus, ExecutionState, OverallStatus
from orchestrator.validation_workspace import run_validation_in_copy, write_validation_json


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
def test_v2_validator_runs_all_declarations_against_staged_overlay(
    monkeypatch, tmp_path, provider_runtime
):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "base.py").write_text("base = True\n", encoding="utf-8")
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    (staging_dir / "base.py").write_text("base = False\n", encoding="utf-8")

    config = TargetConfig(
        schema_version="2.0",
        target_path=project_root,
        workspace_path=tmp_path / "workspace",
        validators=[_validator("lint", "ruff"), _validator("tests", "pytest")],
    )
    seen_roots: list[Path] = []

    def record_result(_declaration, root, _timeout):
        seen_roots.append(root)
        assert root != project_root
        assert (root / "base.py").read_text(encoding="utf-8") == "base = False\n"
        return ProcessResult(return_code=0)

    monkeypatch.setattr(adapters, "_raw_result", record_result)
    output, _ = validator_agent.run(
        config=config, staging_dir=staging_dir, runtime=provider_runtime
    )

    assert output.overall_passed is True
    assert len(seen_roots) == 2
    assert len(set(seen_roots)) == 1


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
    assert output.tools[0].role_coverage == {"typecheck": CoverageStatus.DECLARED_ONLY}


@pytest.mark.unit
def test_v2_standard_tsc_is_declared_only(monkeypatch, tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(adapters, "_raw_result", lambda *_: ProcessResult(return_code=0))

    output = run_v2_validators("run-tsc", tmp_path, [_validator("types", "tsc")], 30)

    assert output.tools[0].role_coverage == {"typecheck": CoverageStatus.DECLARED_ONLY}


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
    if adapter in {"flake8", "pylint"}:
        assert captured[0] == [adapter, str(tmp_path)]
    elif adapter == "mypy":
        assert captured[0][:2] == ["mypy", str(tmp_path)]
        assert captured[0][2] == "--cache-dir"
        assert Path(captured[0][3]).name == "mypy-cache"
        assert Path(captured[0][3]).parent != tmp_path
    elif adapter == "unittest":
        assert captured[0][1:5] == ["-I", "-m", "unittest", "discover"]
        assert captured[0][-2:] == ["-s", str(tmp_path)]
    else:
        assert captured[0][-len(expected_command) :] == expected_command


@pytest.mark.unit
def test_v2_python_adapter_uses_private_launcher_and_isolated_mode(monkeypatch, tmp_path):
    captured = []

    def fake_execute(prepared, timeout):
        captured.append(prepared)
        return ProcessResult(return_code=0)

    monkeypatch.setattr(adapters, "execute_process", fake_execute)
    output = run_v2_validators("run-shadow", tmp_path, [_validator("lint", "ruff")], 30)

    assert output.overall_passed is True
    assert captured[0].cwd != tmp_path
    assert captured[0].argv[1:4] == ("-I", "-m", "ruff")
    assert str(tmp_path) in captured[0].argv


@pytest.mark.unit
def test_v2_validator_scratch_cleanup_failure_is_reported(monkeypatch, tmp_path):
    class BrokenTemporaryDirectory:
        def __init__(self, **kwargs):
            assert kwargs["ignore_cleanup_errors"] is True

        def __enter__(self):
            return str(tmp_path / "scratch")

        def __exit__(self, *_args):
            raise ProcessLookupError

    monkeypatch.setattr(adapters.tempfile, "TemporaryDirectory", BrokenTemporaryDirectory)
    monkeypatch.setattr(
        adapters,
        "execute_process",
        lambda *_args: ProcessResult(return_code=0),
    )

    output = run_v2_validators("run-cleanup", tmp_path, [_validator("lint", "ruff")], 30)

    assert output.tools[0].status is ExecutionState.CLEANUP_FAILED


@pytest.mark.unit
def test_v2_workspace_mutation_fails_and_stops_later_validators(monkeypatch, tmp_path):
    def mutate(_declaration, root, _timeout):
        (root / "conftest.py").write_text("# injected\n", encoding="utf-8")
        return ProcessResult(return_code=0)

    monkeypatch.setattr(adapters, "_raw_result", mutate)
    output = run_v2_validators(
        "run-mutation", tmp_path, [_validator("lint"), _validator("tests", "pytest")], 30
    )

    assert [tool.status for tool in output.tools] == [ExecutionState.FAILED, ExecutionState.NOT_RUN]
    assert "workspace changed" in output.tools[0].stderr.lower()


@pytest.mark.unit
def test_v2_validation_copy_skips_legacy_ruff_format(monkeypatch, tmp_path):
    calls = []
    config = TargetConfig(
        schema_version="2.0",
        target_path=tmp_path,
        workspace_path=tmp_path.parent / "workspace",
        validators=[_validator("lint", "ruff")],
    )
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(
        "orchestrator.validation_workspace.run_validator",
        lambda **_kwargs: (run_v2_validators("run-copy", tmp_path, [], 30), {}),
    )

    run_validation_in_copy(tmp_path, config)

    assert calls == []


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
