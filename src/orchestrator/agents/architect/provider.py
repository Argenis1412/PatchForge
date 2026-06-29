"""Architect provider with fallback chain: Claude → Gemini → OpenRouter."""

import time
from pathlib import Path
from typing import Optional

from orchestrator.agents.executor.providers import (
    ProviderChainResult,
    _call_chain,
    _call_claude,
    _call_gemini,
    _call_openrouter,
)
from orchestrator.exceptions import ProviderError
from orchestrator.observability.events import FailureType, log_failure
from orchestrator.observability.logger import log_call

MODEL = "claude-sonnet-4-6"

_MODEL_MAP = {
    "claude": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-flash",
    "openrouter": "openrouter/free",
}

_COST_RATES = {
    "claude": (3.00, 15.00),
    "gemini": (0.075, 0.30),
    "openrouter": (0.0, 0.0),
}

_ARCHITECT_CHAIN = [_call_claude, _call_gemini, _call_openrouter]


def call_claude(
    prompt: str,
    orchestratorel: str,
    logs_dir: Optional[Path] = None,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    stage: str | None = None,
    span_id: str | None = None,
) -> tuple[str, dict, float, str]:
    """Call the architect provider chain. Returns (raw, tokens, cost, model_used)."""
    call_started = time.monotonic()

    chain_result: ProviderChainResult = _call_chain(_ARCHITECT_CHAIN, prompt, run_id or "")

    if chain_result.success is None:
        latency_ms = int((time.monotonic() - call_started) * 1000)
        failures = "; ".join(f"{n}→{e}" for n, e in chain_result.failures)
        log_failure(
            trace_id=trace_id or "",
            run_id=run_id or "",
            stage=stage,
            error_type=FailureType.LLM_ERROR,
            message=f"Architect provider chain exhausted: {failures}",
            source="agent",
            duration_ms=latency_ms,
            logs_dir=logs_dir,
        )
        raise ProviderError(
            "provider_chain", f"[{orchestratorel}] All providers failed: {failures}"
        )

    raw, input_tokens, output_tokens, _ = chain_result.success
    provider_name = chain_result.provider_name or "claude"
    model_used = _MODEL_MAP.get(provider_name, MODEL)

    cost_in, cost_out = _COST_RATES.get(provider_name, (0.0, 0.0))
    cost = (input_tokens / 1_000_000) * cost_in + (output_tokens / 1_000_000) * cost_out

    tokens = {"input": input_tokens, "output": output_tokens}
    latency_ms = int((time.monotonic() - call_started) * 1000)

    log_call(
        agent=orchestratorel,
        prompt=prompt[:500],
        response=raw[:500],
        tokens=tokens,
        cost_usd=cost,
        logs_dir=logs_dir,
        trace_id=trace_id,
        run_id=run_id,
        stage=stage,
        span_id=span_id,
        model=model_used,
        latency_ms=latency_ms,
    )

    return raw, tokens, cost, model_used
