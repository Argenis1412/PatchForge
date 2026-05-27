import pytest
from orchestrator.agents.validator import run
from orchestrator.schemas.validator_output import ValidatorOutput, ToolResult

@pytest.mark.unit
def test_validator_run_returns_tuple(monkeypatch):
    # Tool must be one of 'ruff', 'pytest', 'tsc'
    mock_tool = ToolResult(tool="ruff", passed=True, return_code=0)
    monkeypatch.setattr("orchestrator.agents.validator.run_ruff", lambda *args, **kwargs: mock_tool)
    monkeypatch.setattr("orchestrator.agents.validator.run_pytest", lambda *args, **kwargs: mock_tool)
    monkeypatch.setattr("orchestrator.agents.validator.run_tsc", lambda *args, **kwargs: mock_tool)
    output, meta = run()
    assert isinstance(output, ValidatorOutput)
    assert isinstance(meta, dict)
