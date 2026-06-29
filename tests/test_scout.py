import pytest

from orchestrator.agents.scout import run
from orchestrator.schemas.scout_output import ScoutOutput


@pytest.mark.unit
def test_scout_run_returns_tuple(mock_gemini):
    # Pass 1: returns list of files
    # Pass 2: returns JSON diagnostic
    pass1 = ('["file.py"]', {"input": 1, "output": 1}, 0.01, "gemini-2.5-flash")
    pass2 = (
        '{"hotspots": [{"file": "test.py", "issue": "x", "severity": "low", '
        '"risk_level": "low", "dependencies": []}], "summary": "s", '
        '"risks": ["r"], "recommended_order": ["t1"]}',
        {"input": 1, "output": 1},
        0.01,
        "gemini-2.5-flash",
    )

    mock_gemini.side_effect = [pass1, pass2]

    output, meta = run("target")
    assert isinstance(output, ScoutOutput)
    assert isinstance(meta, dict)


@pytest.mark.unit
def test_scout_meta_has_required_keys(mock_gemini):
    pass1 = ('["file.py"]', {"input": 1, "output": 1}, 0.01, "gemini-2.5-flash")
    pass2 = (
        '{"hotspots": [{"file": "test.py", "issue": "x", "severity": "low", '
        '"risk_level": "low", "dependencies": []}], "summary": "s", '
        '"risks": ["r"], "recommended_order": ["t1"]}',
        {"input": 1, "output": 1},
        0.01,
        "gemini-2.5-flash",
    )

    mock_gemini.side_effect = [pass1, pass2]

    _, meta = run("target")
    for key in ["tokens_input", "tokens_output", "cost_usd", "model_used"]:
        assert key in meta


@pytest.mark.unit
def test_scout_fallback_model_in_meta(mock_gemini):
    pass1 = ('["file.py"]', {"input": 1, "output": 1}, 0.01, "openrouter/free")
    pass2 = (
        '{"hotspots": [{"file": "test.py", "issue": "x", "severity": "low", '
        '"risk_level": "low", "dependencies": []}], "summary": "s", '
        '"risks": ["r"], "recommended_order": ["t1"]}',
        {"input": 1, "output": 1},
        0.01,
        "claude-sonnet-4-6",
    )

    mock_gemini.side_effect = [pass1, pass2]

    _, meta = run("target")
    assert meta["model_used"] == "claude-sonnet-4-6"


@pytest.mark.unit
def test_scout_provider_error_propagates(mock_gemini):
    from orchestrator.exceptions import ProviderError

    mock_gemini.side_effect = ProviderError("provider_chain", "All providers failed")
    with pytest.raises(ProviderError):
        run("target")
