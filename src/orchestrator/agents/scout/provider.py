"""Scout provider with fallback chain: Gemini → OpenRouter → Claude."""

import json
import time
from pathlib import Path

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

MODEL = "gemini-2.5-flash"

_MODEL_MAP = {
    "gemini": "gemini-2.5-flash",
    "openrouter": "openrouter/free",
    "claude": "claude-sonnet-4-6",
}

_COST_RATES = {
    "gemini": (0.075, 0.30),
    "openrouter": (0.0, 0.0),
    "claude": (3.00, 15.00),
}

_SCOUT_CHAIN = [_call_gemini, _call_openrouter, _call_claude]


def call_gemini(
    prompt: str,
    orchestratorel: str,
    logs_dir: Path | None = None,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    stage: str | None = None,
    span_id: str | None = None,
) -> tuple[str, dict, float, str]:
    """Call the scout provider chain. Returns (raw, tokens, cost, model_used)."""
    call_started = time.monotonic()

    all_failures: list[tuple[str, str]] = []
    winning: ProviderChainResult | None = None

    for provider in _SCOUT_CHAIN:
        candidate = _call_chain([provider], prompt, run_id or "")
        if candidate.success is None:
            all_failures.extend(candidate.failures)
            continue
        raw, _in, _out, _ = candidate.success
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            p_name = candidate.provider_name or provider.__name__.removeprefix("_call_")
            all_failures.append((p_name, f"non-JSON: {exc}"))
            log_failure(
                trace_id=trace_id or "",
                run_id=run_id or "",
                stage=stage,
                error_type=FailureType.SCHEMA_VALIDATION_ERROR,
                message=f"Scout provider {p_name} returned non-JSON: {exc}",
                source="agent",
                duration_ms=int((time.monotonic() - call_started) * 1000),
                logs_dir=logs_dir,
            )
            continue
        winning = candidate
        break

    if winning is None:
        latency_ms = int((time.monotonic() - call_started) * 1000)
        failures = "; ".join(f"{n}→{e}" for n, e in all_failures)
        log_failure(
            trace_id=trace_id or "",
            run_id=run_id or "",
            stage=stage,
            error_type=FailureType.LLM_ERROR,
            message=f"Scout provider chain exhausted: {failures}",
            source="agent",
            duration_ms=latency_ms,
            logs_dir=logs_dir,
        )
        raise ProviderError(
            "provider_chain", f"[{orchestratorel}] All providers failed: {failures}"
        )

    raw, input_tokens, output_tokens, _ = winning.success
    provider_name = winning.provider_name or "gemini"

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
