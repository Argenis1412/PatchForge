import logging
from unittest.mock import MagicMock

import pytest

from orchestrator.agents.validator import run
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.validator_output import ToolResult, ValidatorOutput


@pytest.mark.unit
def test_validator_run_returns_tuple(monkeypatch, tmp_path):
    mock_tool = ToolResult(tool="ruff", passed=True, return_code=0)
    monkeypatch.setattr(
        "orchestrator.agents.validator.run_ruff",
        lambda *a, **kw: mock_tool,
    )
    monkeypatch.setattr(
        "orchestrator.agents.validator.run_pytest",
        lambda *a, **kw: mock_tool,
    )
    monkeypatch.setattr(
        "orchestrator.agents.validator.run_tsc",
        lambda *a, **kw: mock_tool,
    )
    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
    output, meta = run(config=config)
    assert isinstance(output, ValidatorOutput)
    assert isinstance(meta, dict)


@pytest.mark.unit
def test_validator_get_logger_uses_shared_helper(tmp_path, monkeypatch):
    import orchestrator.agents.validator as val_mod

    val_mod._logger = None
    for h in list(logging.getLogger("validator").handlers):
        logging.getLogger("validator").removeHandler(h)
        h.close()

    mock = MagicMock(wraps=val_mod.get_file_logger)
    monkeypatch.setattr("orchestrator.agents.validator.get_file_logger", mock)

    val_mod._get_logger(tmp_path)
    mock.assert_called_once_with("validator", tmp_path, "validator.log")
