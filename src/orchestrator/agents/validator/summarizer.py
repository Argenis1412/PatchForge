import os
import time

from orchestrator.circuit_breaker import CircuitBreakerOpenError
from orchestrator.schemas.validator_output import ToolResult

from .logging import _get_logger

MODEL_GEMINI = "gemini-2.5-flash"
COST_PER_SUMMARY = 0.0


def _summarize_errors(failed_tools: list[ToolResult], run_id: str) -> str:
    if not os.getenv("GOOGLE_API_KEY"):
        _get_logger().warning("[%s] GOOGLE_API_KEY not set — skip summary", run_id)
        return "[summary not available — GOOGLE_API_KEY missing]"

    stderr_sections = "\n\n".join(
        f"### {r.tool.upper()} (rc={r.return_code})\n{(r.stderr or r.stdout)[:3000]}"
        for r in failed_tools
    )

    prompt = f"""You are a code quality analyst. Summarize the following tool errors concisely.

Rules:
- Maximum 5 bullet points
- Each bullet: tool name + root cause + file/line if available
- No suggestions, no fixes — only what failed and why
- If the same error repeats, group it

ERRORS
------
{stderr_sections}
"""

    _get_logger().debug(
        "[%s] Gemini summary request | tools=%s", run_id, [r.tool for r in failed_tools]
    )
    t0 = time.perf_counter()

    try:
        from orchestrator.agents.validator import _cb_validator
        from orchestrator.clients.gemini_client import get_gemini_client

        client = get_gemini_client()
        response = _cb_validator.call(
            lambda: client.models.generate_content(
                model=MODEL_GEMINI,
                contents=prompt,
            )
        )
        elapsed = time.perf_counter() - t0
        summary = response.text.strip()

        usage = getattr(response, "usage_metadata", None)
        input_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
        output_tok = getattr(usage, "candidates_token_count", 0) if usage else 0

        _get_logger().info(
            "[%s] Gemini summary OK | latency=%.2fs | in=%d | out=%d | cost=$0.00 (free tier)",
            run_id,
            elapsed,
            input_tok,
            output_tok,
        )
        return summary

    except CircuitBreakerOpenError:
        _get_logger().warning("[%s] Gemini CB open — using raw stderr fallback", run_id)
        return "\n".join(f"[{r.tool}] {(r.stderr or r.stdout)[:500]}" for r in failed_tools)

    except Exception as exc:
        _get_logger().error("[%s] Gemini summary failed: %s — using raw stderr", run_id, exc)
        return "\n".join(f"[{r.tool}] {(r.stderr or r.stdout)[:500]}" for r in failed_tools)
