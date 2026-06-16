"""Claude (Anthropic) provider wrapper with retry, cost tracking, and observability."""

import time
from pathlib import Path
from typing import Optional

from orchestrator.clients.anthropic_client import get_anthropic_client
from orchestrator.exceptions import ProviderError
from orchestrator.observability.events import FailureType, log_failure
from orchestrator.observability.logger import log_call

MODEL = "claude-sonnet-4-6"

COST_PER_1M_INPUT = 3.00
COST_PER_1M_OUTPUT = 15.00


def call_claude(
    prompt: str,
    orchestratorel: str,
    logs_dir: Optional[Path] = None,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    stage: str | None = None,
    span_id: str | None = None,
) -> tuple[str, dict, float]:
    """Wrapper with retry and logging for Claude."""
    client = get_anthropic_client()
    from anthropic import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

    for attempt in range(2):
        call_started = time.monotonic()
        try:
            response = client.messages.create(
                model=MODEL, max_tokens=4096, messages=[{"role": "user", "content": prompt}]
            )
            latency_ms = int((time.monotonic() - call_started) * 1000)
            raw = response.content[0].text.strip()

            tokens = {
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
            }
            cost = (
                tokens["input"] / 1_000_000 * COST_PER_1M_INPUT
                + tokens["output"] / 1_000_000 * COST_PER_1M_OUTPUT
            )

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
                model=MODEL,
                latency_ms=latency_ms,
            )

            return raw, tokens, cost

        except RateLimitError as e:
            latency_ms = int((time.monotonic() - call_started) * 1000)
            if attempt == 0:
                # attempt 0: retry without logging — not a terminal failure
                print(f"[{orchestratorel}] Rate limit. Waiting 60s...")
                time.sleep(60)
                continue

            log_call(
                agent=orchestratorel,
                prompt=prompt[:500],
                response="",
                tokens={"input": 0, "output": 0},
                cost_usd=0.0,
                logs_dir=logs_dir,
                trace_id=trace_id,
                run_id=run_id,
                stage=stage,
                span_id=span_id,
                model=MODEL,
                latency_ms=latency_ms,
                error=str(e),
            )
            log_failure(
                trace_id=trace_id or "",
                run_id=run_id or "",
                stage=stage,
                error_type=FailureType.LLM_ERROR,
                message=f"Claude call {orchestratorel} failed: {e}",
                source="agent",
                duration_ms=latency_ms,
                logs_dir=logs_dir,
            )
            raise ProviderError("anthropic", f"[{orchestratorel}] Failed: {e}")

        except (APIConnectionError, APITimeoutError, APIStatusError) as e:
            latency_ms = int((time.monotonic() - call_started) * 1000)
            log_call(
                agent=orchestratorel,
                prompt=prompt[:500],
                response="",
                tokens={"input": 0, "output": 0},
                cost_usd=0.0,
                logs_dir=logs_dir,
                trace_id=trace_id,
                run_id=run_id,
                stage=stage,
                span_id=span_id,
                model=MODEL,
                latency_ms=latency_ms,
                error=str(e),
            )
            log_failure(
                trace_id=trace_id or "",
                run_id=run_id or "",
                stage=stage,
                error_type=FailureType.LLM_ERROR,
                message=f"Claude call {orchestratorel} failed: {e}",
                source="agent",
                duration_ms=latency_ms,
                logs_dir=logs_dir,
            )
            raise ProviderError("anthropic", f"[{orchestratorel}] Failed: {e}")

    raise ProviderError("anthropic", f"[{orchestratorel}] Failed after retry.")
