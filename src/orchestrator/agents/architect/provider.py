"""Architect provider with fallback chain: Claude → Gemini → OpenRouter."""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from orchestrator.agents.executor.providers import (
    ProviderChainResult,
    _call_chain,
    _call_claude,
    _call_gemini,
    _call_openrouter,
    _get_model,
    _provider_by_name,
)
from orchestrator.exceptions import ProviderError
from orchestrator.observability.events import FailureType, log_failure
from orchestrator.observability.logger import log_call

_ARCHITECT_CHAIN = [_call_claude, _call_gemini, _call_openrouter]


@dataclass
class ArchitectCallResult:
    raw: str
    tokens: dict
    cost: float | None
    model_used: str
    provider_name: str | None = None
    primary_provider_attempted: str | None = None
    primary_failure_category: str | None = None


def call_claude(
    prompt: str,
    orchestratorel: str,
    logs_dir: Optional[Path] = None,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    stage: str | None = None,
    span_id: str | None = None,
    force_provider: str | None = None,
) -> ArchitectCallResult:
    """Call the architect provider chain."""
    call_started = time.monotonic()

    if force_provider:
        by_name = _provider_by_name()
        provider = by_name.get(force_provider)
        if provider is None:
            raise ProviderError(
                "provider_chain",
                f"Unknown provider: {force_provider}. Available: {tuple(sorted(by_name))}",
            )
        chain = [provider]
    else:
        chain = _ARCHITECT_CHAIN

    chain_result: ProviderChainResult = _call_chain(chain, prompt, run_id or "")

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

    raw, input_tokens, output_tokens, cost = chain_result.success
    provider_name = chain_result.provider_name or "claude"
    model_used = _get_model(provider_name)

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

    return ArchitectCallResult(
        raw=raw,
        tokens=tokens,
        cost=cost,
        model_used=model_used,
        provider_name=provider_name,
        primary_provider_attempted=chain_result.primary_provider_attempted,
        primary_failure_category=chain_result.primary_failure_category,
    )
