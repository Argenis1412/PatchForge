# agents/architect.py
import json
import sys
import time
from pathlib import Path
from typing import Optional, Union

from orchestrator.clients.anthropic_client import get_anthropic_client
from orchestrator.exceptions import ProviderError
from orchestrator.llm.parser import LLMParseError, SchemaValidationError, parse_llm_response
from orchestrator.observability.events import FailureType, log_failure
from orchestrator.observability.logger import log_call
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.scout_output import ScoutOutput

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

        except Exception as e:
            latency_ms = int((time.monotonic() - call_started) * 1000)
            if "rate" in str(e).lower() and attempt == 0:
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

    raise ProviderError("anthropic", f"[{orchestratorel}] Failed after retry.")


ARCHITECT_PROMPT = """
You are the Architect Agent. Your job is to analyze the reconnaissance data
provided by the Scout Agent.
Given this Scout diagnosis, your job is to:
1. Validate findings — detect false positives
2. Prioritize by real impact vs effort
3. Detect systemic risks the Scout missed
4. Design a safe implementation order
5. Identify what blocks Phase 2 of the Engineering Playbook

Do not implement anything.
Output: ONLY valid JSON matching this exact schema. No explanation. No markdown:
{{
  "validated_findings": ["string"],
  "false_positives": ["string"],
  "systemic_risks": ["string"],
  "implementation_plan": [
    {{
      "task_id": "string",
      "title": "string",
      "description": "string",
      "files_to_modify": ["string"],
      "priority": "high|medium|low",
      "effort": "high|medium|low",
      "risk_level": "high|medium|low",
      "reason": "string",
      "risk_reasons": ["string"],
      "validation_expectations": ["string"],
      "dependencies": ["string"]
    }}
  ],
  "blockers": ["string"]
}}

All fields are required. Do not omit any field.

[SCOUT OUTPUT]
{scout_data}
"""


def run(
    scout_output: ScoutOutput,
    config: Optional[Union[str, Path, TargetConfig]] = None,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
) -> tuple[ArchitectOutput, dict]:
    logs_dir: Optional[Path] = None
    if config is not None:
        if isinstance(config, (str, Path)):
            config = TargetConfig.load(target_path=Path(config))
        logs_dir = config.workspace_path / "logs"

    print("[Architect] Processing ScoutOutput object...")
    scout_data = scout_output.model_dump_json()
    print(f"[Architect] Asking {MODEL} to structure the implementation plan...")

    raw_response, tokens, cost = call_claude(
        ARCHITECT_PROMPT.format(scout_data=scout_data),
        orchestratorel="architect",
        logs_dir=logs_dir,
        trace_id=trace_id,
        run_id=run_id,
        stage="architect",
        span_id="architect",
    )

    print(f"[Architect] Done | tokens: {tokens} | cost: ${cost:.5f}")

    # Validate JSON via canonical parser
    try:
        output = parse_llm_response(raw_response, ArchitectOutput)
    except LLMParseError as e:
        print(f"[Architect] JSON parse error: {e}")
        print(f"[Architect] Raw output:\n{raw_response}")
        log_failure(
            trace_id=trace_id or "",
            run_id=run_id or "",
            stage="architect",
            error_type=FailureType.SCHEMA_VALIDATION_ERROR,
            message=f"Architect JSON parsing failed: {e}",
            source="agent",
            logs_dir=logs_dir,
        )
        raise
    except SchemaValidationError as e:
        print(f"[Architect] Schema validation error: {e}")
        log_failure(
            trace_id=trace_id or "",
            run_id=run_id or "",
            stage="architect",
            error_type=FailureType.SCHEMA_VALIDATION_ERROR,
            message=f"Architect schema validation failed: {e}",
            source="agent",
            logs_dir=logs_dir,
        )
        raise

    meta = {
        "tokens_input": tokens["input"],
        "tokens_output": tokens["output"],
        "cost_usd": cost,
        "model_used": MODEL,
    }

    return output, meta


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agents/architect.py <path_to_scout_output.json>")
        sys.exit(1)

    scout_path = sys.argv[1]
    with open(scout_path, "r") as f:
        scout_data = ScoutOutput.model_validate_json(f.read())

    result, _ = run(scout_data)
    print("\n-- Architect Output --")
    print(json.dumps(result.model_dump(), indent=2))
