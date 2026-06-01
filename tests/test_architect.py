import pytest

from orchestrator.agents.architect import run
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.scout_output import ScoutOutput


@pytest.mark.unit
def test_architect_run_returns_tuple(mock_claude):
    # Mocking return value of call_claude to match (str, dict, float)
    json_output = '{"validated_findings": [], "false_positives": [], "systemic_risks": [], "implementation_plan": [], "blockers": []}'
    mock_claude.return_value = (json_output, {"input": 1, "output": 1}, 0.01)

    scout_out = ScoutOutput(hotspots=[], summary="s", risks=["r"], recommended_order=[])
    output, meta = run(scout_out)
    assert isinstance(output, ArchitectOutput)
    assert isinstance(meta, dict)
