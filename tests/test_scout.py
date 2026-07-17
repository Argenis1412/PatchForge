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


# ---------------------------------------------------------------------------
# Provider chain unit tests — exercise scout/provider.py:call_gemini directly
# ---------------------------------------------------------------------------

_VALID_JSON = '{"hotspots": [], "summary": "s", "risks": [], "recommended_order": []}'


@pytest.mark.unit
def test_scout_provider_non_json_raises(monkeypatch):
    """All providers returning non-JSON must raise ProviderError."""
    from orchestrator.agents.executor.providers import ProviderChainResult
    from orchestrator.agents.scout import provider as scout_provider
    from orchestrator.exceptions import ProviderError

    non_json = ProviderChainResult(
        success=("Here is some explanatory text, not JSON.", 10, 5, 0.0),
        provider_name="openrouter",
    )
    monkeypatch.setattr(scout_provider, "_call_chain", lambda *a, **kw: non_json)
    monkeypatch.setattr(scout_provider, "log_failure", lambda *a, **kw: None)

    with pytest.raises(ProviderError):
        scout_provider.call_gemini("prompt", "scout")


@pytest.mark.unit
def test_scout_provider_non_json_falls_back(monkeypatch):
    """Non-JSON from first provider causes the chain to try the next provider."""
    from orchestrator.agents.executor.providers import ProviderChainResult
    from orchestrator.agents.scout import provider as scout_provider

    calls = []

    def fake_call_chain(chain, prompt, run_id):
        calls.append(chain[0].__name__)
        if chain[0].__name__ == "_call_gemini":
            return ProviderChainResult(
                success=("not valid json at all", 10, 5, 0.0),
                provider_name="gemini",
            )
        return ProviderChainResult(
            success=(_VALID_JSON, 10, 5, 0.0),
            provider_name="openrouter",
        )

    monkeypatch.setattr(scout_provider, "_call_chain", fake_call_chain)
    monkeypatch.setattr(scout_provider, "log_failure", lambda *a, **kw: None)
    monkeypatch.setattr(scout_provider, "log_call", lambda *a, **kw: None)

    raw, tokens, cost, model_used = scout_provider.call_gemini("prompt", "scout")

    assert "_call_gemini" in calls
    assert "_call_openrouter" in calls
    assert raw == _VALID_JSON
    assert model_used == "openrouter/free"


@pytest.mark.unit
def test_scout_provider_claude_uses_registry_resolved_model_and_chain_cost(monkeypatch):
    """Issue #246: scout no longer recomputes cost locally — it uses whatever
    _call_chain already computed, and resolves model_used through the shared
    Provider Registry (_get_model)."""
    from orchestrator.agents.executor.providers import ProviderChainResult
    from orchestrator.agents.scout import provider as scout_provider

    chain_result = ProviderChainResult(
        success=(_VALID_JSON, 1_000_000, 1_000_000, 18.0),
        provider_name="claude",
    )
    monkeypatch.setattr(scout_provider, "_call_chain", lambda *a, **kw: chain_result)
    monkeypatch.setattr(scout_provider, "log_call", lambda *a, **kw: None)

    raw, tokens, cost, model_used = scout_provider.call_gemini("prompt", "scout")

    assert raw == _VALID_JSON
    assert model_used == "claude-sonnet-4-6"
    assert cost == pytest.approx(18.0)


@pytest.mark.unit
def test_scout_provider_gemini_uses_registry_resolved_model_and_chain_cost(monkeypatch):
    from orchestrator.agents.executor.providers import ProviderChainResult
    from orchestrator.agents.scout import provider as scout_provider

    chain_result = ProviderChainResult(
        success=(_VALID_JSON, 1_000_000, 1_000_000, 0.375),
        provider_name="gemini",
    )
    monkeypatch.setattr(scout_provider, "_call_chain", lambda *a, **kw: chain_result)
    monkeypatch.setattr(scout_provider, "log_call", lambda *a, **kw: None)

    _, _, cost, model_used = scout_provider.call_gemini("prompt", "scout")

    assert model_used == "gemini-2.5-flash"
    assert cost == pytest.approx(0.375)


@pytest.mark.unit
def test_scout_provider_none_cost_propagates_when_model_overridden(monkeypatch):
    """AC6: when _call_chain reports cost=None (overridden model with an
    unknown cost table), scout must propagate None, not coerce to 0.0."""
    from orchestrator.agents.executor.providers import ProviderChainResult
    from orchestrator.agents.scout import provider as scout_provider

    chain_result = ProviderChainResult(
        success=(_VALID_JSON, 1_000_000, 1_000_000, None),
        provider_name="claude",
    )
    monkeypatch.setattr(scout_provider, "_call_chain", lambda *a, **kw: chain_result)
    logged_costs = []
    monkeypatch.setattr(
        scout_provider,
        "log_call",
        lambda *a, **kw: logged_costs.append(kw.get("cost_usd")),
    )

    _, _, cost, _ = scout_provider.call_gemini("prompt", "scout")

    assert cost is None
    assert logged_costs == [None]


@pytest.mark.unit
def test_scout_provider_chain_exhausted_raises(monkeypatch):
    from orchestrator.agents.executor.providers import ProviderChainResult
    from orchestrator.agents.scout import provider as scout_provider
    from orchestrator.exceptions import ProviderError

    chain_result = ProviderChainResult(
        success=None,
        failures=[("_call_gemini", "down"), ("_call_openrouter", "down")],
    )
    monkeypatch.setattr(scout_provider, "_call_chain", lambda *a, **kw: chain_result)
    monkeypatch.setattr(scout_provider, "log_failure", lambda *a, **kw: None)

    with pytest.raises(ProviderError):
        scout_provider.call_gemini("prompt", "scout")
