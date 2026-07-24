"""Configuration-contract coverage for validator plugins phase 1 (issue #282)."""

import json

import pytest

from orchestrator.agents.validator.process import prepare_process
from orchestrator.schemas.config import (
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    TargetConfig,
    ValidatorRole,
)


def _workspace(path):
    return path.parent / f"{path.name}-workspace"


def _write_config(path, data):
    (path / "orchestrator.json").write_text(json.dumps(data), encoding="utf-8")


def test_versionless_config_is_loaded_as_legacy_profile(tmp_path):
    _write_config(tmp_path, {"lint_command": ["ruff", "check", "."]})

    config = TargetConfig.load(tmp_path, workspace_path=_workspace(tmp_path))

    assert config.schema_version == LEGACY_SCHEMA_VERSION
    assert config.lint_command == ["ruff", "check", "."]
    assert config.validators is None


def test_explicit_v1_config_is_loaded_as_legacy_profile(tmp_path):
    _write_config(
        tmp_path,
        {"schema_version": LEGACY_SCHEMA_VERSION, "test_command": ["pytest", "tests"]},
    )

    config = TargetConfig.load(tmp_path, workspace_path=_workspace(tmp_path))

    assert config.schema_version == LEGACY_SCHEMA_VERSION
    assert config.test_command == ["pytest", "tests"]


def test_direct_legacy_config_rejects_validators(tmp_path):
    with pytest.raises(ValueError, match="requires orchestrator.json schema_version 2.0"):
        TargetConfig(
            schema_version=LEGACY_SCHEMA_VERSION,
            target_path=tmp_path,
            workspace_path=_workspace(tmp_path),
            validators=[{"id": "lint", "adapter": "ruff"}],
        )


def test_legacy_unknown_fields_remain_ignored(tmp_path):
    _write_config(tmp_path, {"legacy_extension": {"enabled": True}})

    config = TargetConfig.load(tmp_path, workspace_path=_workspace(tmp_path))

    assert config.schema_version == LEGACY_SCHEMA_VERSION


def test_v2_config_assigns_fixed_adapter_roles(tmp_path):
    _write_config(
        tmp_path,
        {
            "schema_version": SCHEMA_VERSION,
            "validators": [
                {"id": "lint", "adapter": "ruff"},
                {"id": "types", "adapter": "mypy"},
            ],
        },
    )

    config = TargetConfig.load(tmp_path, workspace_path=_workspace(tmp_path))

    assert [validator.id for validator in config.validators] == ["lint", "types"]
    assert config.validators[0].roles == [ValidatorRole.LINT]
    assert config.validators[1].roles == [ValidatorRole.TYPECHECK]


def test_v2_config_rejects_empty_validator_declarations(tmp_path):
    _write_config(tmp_path, {"schema_version": SCHEMA_VERSION, "validators": []})

    with pytest.raises(ValueError, match="at least one declaration"):
        TargetConfig.load(tmp_path, workspace_path=_workspace(tmp_path))


def test_v2_command_requires_declared_roles_and_argv(tmp_path):
    _write_config(
        tmp_path,
        {
            "schema_version": SCHEMA_VERSION,
            "validators": [
                {
                    "id": "integration",
                    "adapter": "command",
                    "roles": ["test"],
                    "command": ["integration-test", "--quiet"],
                }
            ],
        },
    )

    config = TargetConfig.load(tmp_path, workspace_path=_workspace(tmp_path))

    assert config.validators[0].roles == [ValidatorRole.TEST]
    assert config.validators[0].success_codes == [0]


def test_v2_builtin_adapter_accepts_command_override(tmp_path):
    _write_config(
        tmp_path,
        {
            "schema_version": SCHEMA_VERSION,
            "validators": [{"id": "lint", "adapter": "ruff", "command": ["ruff", "check", "src"]}],
        },
    )

    config = TargetConfig.load(tmp_path, workspace_path=_workspace(tmp_path))

    assert config.validators[0].command == ["ruff", "check", "src"]


@pytest.mark.parametrize(
    "data, message",
    [
        ({"validators": []}, "requires orchestrator.json schema_version 2.0"),
        ({"schema_version": "3.0"}, "Unsupported"),
        ({"schema_version": SCHEMA_VERSION, "unknown": True}, "unknown fields"),
        (
            {"schema_version": SCHEMA_VERSION, "validators": [{"id": "x", "adapter": "wat"}]},
            "adapter",
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "validators": [
                    {"id": "same", "adapter": "ruff"},
                    {"id": "same", "adapter": "pytest"},
                ],
            },
            "Validator ids must be unique",
        ),
    ],
)
def test_invalid_validator_configuration_fails_visibly(tmp_path, data, message):
    _write_config(tmp_path, data)

    with pytest.raises(ValueError, match=message):
        TargetConfig.load(tmp_path, workspace_path=_workspace(tmp_path))


def test_invalid_json_fails_visibly(tmp_path):
    (tmp_path / "orchestrator.json").write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid orchestrator.json"):
        TargetConfig.load(tmp_path, workspace_path=_workspace(tmp_path))


def test_prepare_process_freezes_argv_without_shell(tmp_path):
    prepared = prepare_process(["ruff", "check", "."], tmp_path)

    assert prepared.argv == ("ruff", "check", ".")
    assert prepared.cwd == tmp_path
    assert prepared.env is None


def test_prepare_process_freezes_environment(tmp_path):
    environment = {"PATH": "one"}
    prepared = prepare_process(["tool"], tmp_path, environment=environment)
    environment["PATH"] = "two"

    assert prepared.env["PATH"] == "one"


def test_prepare_process_rejects_empty_arguments(tmp_path):
    with pytest.raises(ValueError, match="non-empty arguments"):
        prepare_process(["ruff", ""], tmp_path)
