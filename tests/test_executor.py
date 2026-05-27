import pytest
from orchestrator.agents.executor import run
from orchestrator.schemas.architect_output import ArchitectOutput

@pytest.mark.unit
def test_executor_run_returns_tuple(mock_gemini):
    # Mocking _call_gemini as a fallback if not using groq/claude logic
    mock_gemini.return_value = {"applied": [], "errors": [], "pending_review": []}
    arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[],
        blockers=[]
    )
    output, meta = run(arch_out)
    assert isinstance(meta, dict)
