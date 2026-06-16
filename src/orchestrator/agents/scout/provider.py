"""Gemini (Google) provider wrapper with retry, cost tracking, and markdown stripping."""

import time
from pathlib import Path

from orchestrator.clients.gemini_client import get_gemini_client
from orchestrator.exceptions import ProviderError
from orchestrator.observability.events import FailureType, log_failure
from orchestrator.observability.logger import log_call

MODEL = "gemini-2.5-flash"

COST_PER_1M_INPUT = 0.075
COST_PER_1M_OUTPUT = 0.30


def call_gemini(
    prompt: str,
    orchestratorel: str,
    logs_dir: Path | None = None,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    stage: str | None = None,
    span_id: str | None = None,
) -> tuple[str, dict, float]:
    client = get_gemini_client()
    from google.genai.errors import APIError, ClientError, ServerError

    for attempt in range(2):
        call_started = time.monotonic()
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
            )
            latency_ms = int((time.monotonic() - call_started) * 1000)
            raw = response.text.strip()

            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            usage = response.usage_metadata
            tokens = {
                "input": usage.prompt_token_count,
                "output": usage.candidates_token_count,
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

        except (APIError, ClientError, ServerError) as e:
            latency_ms = int((time.monotonic() - call_started) * 1000)
            if isinstance(e, ClientError) and e.code == 429:
                if attempt == 0:
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
                message=f"Gemini call {orchestratorel} failed: {e}",
                source="agent",
                duration_ms=latency_ms,
                logs_dir=logs_dir,
            )
            break

    raise ProviderError("gemini", f"[{orchestratorel}] Failed after retry.")
