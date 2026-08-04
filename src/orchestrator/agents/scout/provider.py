"""Scout provider with fallback chain: Gemini → OpenRouter → Claude."""

import json
import time
from pathlib import Path

from orchestrator.agents.executor.providers import (
    ProviderChainResult,
    _call_chain,
    _provider_by_name,
)
from orchestrator.exceptions import ProviderError
from orchestrator.observability.events import FailureType, log_event, log_failure
from orchestrator.observability.logger import log_call
from orchestrator.provider_policy import provider_chain
from orchestrator.provider_runtime import ProviderRuntime


def call_gemini(
    prompt: str,
    orchestratorel: str,
    logs_dir: Path | None = None,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    stage: str | None = None,
    span_id: str | None = None,
    runtime: ProviderRuntime,
) -> tuple[str, dict, float | None, str]:
    """Call the scout provider chain. Returns (raw, tokens, cost, model_used)."""
    call_started = time.monotonic()

    all_failures: list[tuple[str, str]] = []
    winning: ProviderChainResult | None = None

    def _record_static_skip(provider_name: str) -> None:
        if logs_dir is None:
            return
        log_event(
            trace_id=trace_id or "",
            run_id=run_id or "",
            source="scout",
            stage=stage,
            event="provider_skipped_static_ineligible",
            data={"provider": provider_name, "cause": "static_ineligible"},
            logs_dir=logs_dir,
        )

    for provider_name in provider_chain("scout"):
        provider = _provider_by_name()[provider_name]
        candidate = _call_chain(
            [provider],
            runtime,
            prompt,
            run_id or "",
            on_static_skip=_record_static_skip,
        )
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

    raw, input_tokens, output_tokens, cost = winning.success
    provider_name = winning.provider_name or "gemini"

    model_used = runtime.model_for(provider_name)

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
