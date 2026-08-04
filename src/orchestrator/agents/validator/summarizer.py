"""LLM-based validation-error summaries using the invocation runtime."""

from __future__ import annotations

from collections.abc import Callable

from orchestrator.agents.executor.providers import _call_chain, _provider_by_name
from orchestrator.provider_policy import provider_chain
from orchestrator.provider_runtime import ProviderRuntime
from orchestrator.schemas.validator_output import ToolResult

from .logging import _get_logger


def _summarize_errors(
    failed_tools: list[ToolResult],
    run_id: str,
    runtime: ProviderRuntime,
    on_static_skip: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    """Return a provider summary, or raw stderr when no provider succeeds."""
    stderr_sections = "\n\n".join(
        f"### {result.tool.upper()} (rc={result.return_code})\n"
        f"{(result.stderr or result.stdout)[:3000]}"
        for result in failed_tools
    )
    prompt = f"""You are a code quality analyst. Summarize the following tool errors concisely.

Rules:
- Maximum 5 bullet points
- Each bullet: tool name + root cause + file/line if available
- No suggestions, no fixes — only what failed and why

ERRORS
------
{stderr_sections}
"""
    try:
        chain = [_provider_by_name()[name] for name in provider_chain("validator_summary")]
        result = _call_chain(chain, runtime, prompt, run_id, on_static_skip=on_static_skip)
    except Exception as exc:
        _get_logger().warning("[%s] Validator summary failed: %s", run_id, exc)
    else:
        if result.success is not None:
            return result.success[0], result.provider_name or ""
    raw = "\n".join(
        f"[{result.tool}] {(result.stderr or result.stdout)[:500]}" for result in failed_tools
    )
    return raw, ""
