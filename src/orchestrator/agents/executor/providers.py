"""Multi-provider LLM chain (Gemini, OpenRouter, Claude) with circuit breakers and fallback."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    circuit_breaker_for,
)
from orchestrator.clients.anthropic_client import get_anthropic_client
from orchestrator.clients.gemini_client import get_gemini_client
from orchestrator.clients.openrouter_client import get_openrouter_client
from orchestrator.storage.lock import SqliteCircuitBreakerStore

from .logging import _get_logger

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL_GEMINI = "gemini-2.5-flash"
MODEL_OPENROUTER = "openrouter/free"
MODEL_CLAUDE = "claude-sonnet-4-6"

COST_PER_1M_INPUT_CLAUDE = 3.00
COST_PER_1M_OUTPUT_CLAUDE = 15.00

MAX_RETRIES = 1

# Provider fallback chain per risk level.
# Each list is tried in order; the first provider to return a valid
# non-empty response wins.  HIGH risk has no fallback by policy:
# if Claude is unavailable the task must fail rather than silently
# degrade to a less capable model.
_PROVIDER_CHAIN: dict[str, list] = {
    "low": [],
    "medium": [],
    "high": [],
}

# ---------------------------------------------------------------------------
# Shared circuit breakers — backed by coordination.db (SQLite).
# State persists across restarts and is visible to all workers on the same host.
# Single-threaded use only: SqliteCircuitBreakerStore is not thread-safe.
# _half_open_in_flight is process-local; multiple workers may probe simultaneously
# in HALF_OPEN — acceptable since a successful probe closes the CB for everyone.
# ---------------------------------------------------------------------------

_coord_store: SqliteCircuitBreakerStore | None = None
_cb_gemini: CircuitBreaker | None = None
_cb_openrouter: CircuitBreaker | None = None
_cb_claude: CircuitBreaker | None = None


def _init_circuit_breakers() -> None:
    """Lazy-init shared store + circuit breakers on first use (not at import time)."""
    global _coord_store, _cb_gemini, _cb_openrouter, _cb_claude  # noqa: PLW0603
    if _coord_store is not None:
        return
    db_dir_env = os.getenv("PATCHFORGE_DATA_DIR")
    coord_db_dir = Path(db_dir_env) if db_dir_env is not None else Path.home() / ".patchforge"
    _coord_store = SqliteCircuitBreakerStore(coord_db_dir)
    _cb_gemini = circuit_breaker_for("gemini", store=_coord_store)
    _cb_openrouter = circuit_breaker_for("openrouter", store=_coord_store)
    _cb_claude = circuit_breaker_for("claude", store=_coord_store)


# ---------------------------------------------------------------------------
# Model Helpers
# ---------------------------------------------------------------------------


def _strip_markdown(content: str) -> str:
    if content.startswith("```"):
        parts = content.split("```")
        if len(parts) >= 3:
            content = parts[1]
            if "\n" in content:
                content = content.split("\n", 1)[1]
    return content.strip()


def _is_valid_provider_response(raw: str) -> bool:
    return bool(raw and raw.strip())


def _compute_cost(provider, input_tokens: int, output_tokens: int) -> float:
    if provider is _call_claude:
        return (input_tokens / 1_000_000) * COST_PER_1M_INPUT_CLAUDE + (
            output_tokens / 1_000_000
        ) * COST_PER_1M_OUTPUT_CLAUDE
    return 0.0


# ---------------------------------------------------------------------------
# Provider calls
# ---------------------------------------------------------------------------


def _do_gemini_call(prompt: str, run_id: str) -> tuple[str, int, int]:
    from google.genai import types

    client = get_gemini_client()
    log = _get_logger()
    log.debug("[%s] Gemini request | model=%s | prompt_chars=%d", run_id, MODEL_GEMINI, len(prompt))

    t0 = time.perf_counter()
    response = client.models.generate_content(
        model=MODEL_GEMINI, contents=prompt, config=types.GenerateContentConfig(temperature=0.0)
    )
    elapsed = time.perf_counter() - t0

    content = _strip_markdown(response.text)

    usage = response.usage_metadata
    input_tokens = usage.prompt_token_count if usage else 0
    output_tokens = usage.candidates_token_count if usage else 0

    log.info(
        "[%s] Gemini OK | latency=%.2fs | in=%d | out=%d",
        run_id,
        elapsed,
        input_tokens,
        output_tokens,
    )

    return content, input_tokens, output_tokens


def _call_gemini(prompt: str, run_id: str) -> tuple[str, int, int]:
    _init_circuit_breakers()
    return _cb_gemini.call(lambda: _do_gemini_call(prompt, run_id))


def _do_openrouter_call(prompt: str, run_id: str) -> tuple[str, int, int]:
    log = _get_logger()
    client = get_openrouter_client()
    headers = {
        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL_OPENROUTER,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }

    log.debug(
        "[%s] OpenRouter request | model=%s | prompt_chars=%d",
        run_id,
        MODEL_OPENROUTER,
        len(prompt),
    )

    t0 = time.perf_counter()
    response = client.post(
        "/chat/completions",
        headers=headers,
        json=payload,
    )
    response.raise_for_status()

    elapsed = time.perf_counter() - t0
    data = response.json()

    content = _strip_markdown(data["choices"][0]["message"]["content"])

    usage = data.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    log.info(
        "[%s] OpenRouter OK | latency=%.2fs | in=%d | out=%d",
        run_id,
        elapsed,
        input_tokens,
        output_tokens,
    )

    return content, input_tokens, output_tokens


def _call_openrouter(prompt: str, run_id: str) -> tuple[str, int, int]:
    _init_circuit_breakers()
    return _cb_openrouter.call(lambda: _do_openrouter_call(prompt, run_id))


def _do_claude_call(prompt: str, run_id: str) -> tuple[str, int, int]:
    client = get_anthropic_client()
    log = _get_logger()
    log.debug("[%s] Claude request | model=%s | prompt_chars=%d", run_id, MODEL_CLAUDE, len(prompt))

    t0 = time.perf_counter()
    response = client.messages.create(
        model=MODEL_CLAUDE,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    elapsed = time.perf_counter() - t0

    content = _strip_markdown(response.content[0].text)

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    log.info(
        "[%s] Claude OK | latency=%.2fs | in=%d | out=%d",
        run_id,
        elapsed,
        input_tokens,
        output_tokens,
    )

    return content, input_tokens, output_tokens


def _call_claude(prompt: str, run_id: str) -> tuple[str, int, int]:
    _init_circuit_breakers()
    return _cb_claude.call(lambda: _do_claude_call(prompt, run_id))


# ---------------------------------------------------------------------------
# Provider fallback chain (populated after all _call_* defs)
# ---------------------------------------------------------------------------

_PROVIDER_CHAIN["low"] = [_call_gemini, _call_openrouter, _call_claude]
_PROVIDER_CHAIN["medium"] = [_call_openrouter, _call_gemini, _call_claude]
_PROVIDER_CHAIN["high"] = [_call_claude]


def _provider_by_name() -> dict[str, object]:
    # Single source of truth: derived from _PROVIDER_CHAIN, not a manual list.
    # Any _call_* added to _PROVIDER_CHAIN is automatically available via
    # --force-provider.  No second registry to keep in sync.
    out: dict[str, object] = {}
    for chain in _PROVIDER_CHAIN.values():
        for fn in chain:
            short = fn.__name__.removeprefix("_call_")
            out[short] = fn
    return out


KNOWN_PROVIDER_NAMES: tuple[str, ...] = tuple(sorted(_provider_by_name().keys()))


# ---------------------------------------------------------------------------
# Provider chain result
# ---------------------------------------------------------------------------


@dataclass
class ProviderChainResult:
    success: tuple[str, int, int, float] | None = None
    failures: list[tuple[str, str]] = field(default_factory=list)
    provider_name: str | None = None


def _recoverable_exceptions() -> tuple:
    if not hasattr(_recoverable_exceptions, "_cache"):
        import anthropic as _anthropic
        import httpx as _httpx
        from google.genai.errors import APIError as _GeminiAPIError

        _recoverable_exceptions._cache = (
            CircuitBreakerOpenError,
            _GeminiAPIError,
            _httpx.HTTPError,
            _anthropic.APIError,
        )
    return _recoverable_exceptions._cache


def _call_chain(chain: list, prompt: str, run_id: str) -> ProviderChainResult:
    failures: list[tuple[str, str]] = []
    for provider in chain:
        try:
            raw, input_tokens, output_tokens = provider(prompt, run_id)
            if not _is_valid_provider_response(raw):
                failures.append((provider.__name__, "invalid/empty response"))
                _get_logger().warning(
                    "[%s] Invalid/empty response from %s, trying next",
                    run_id,
                    provider.__name__,
                )
                continue
            cost = _compute_cost(provider, input_tokens, output_tokens)
            return ProviderChainResult(
                success=(raw, input_tokens, output_tokens, cost),
                failures=failures,
                provider_name=provider.__name__.removeprefix("_call_"),
            )
        except _recoverable_exceptions() as exc:
            failures.append((provider.__name__, str(exc)))
            _get_logger().info(
                "[%s] %s unavailable: %s, trying next",
                run_id,
                provider.__name__,
                exc,
            )
            continue

    summary = "; ".join(f"{name}→{err}" for name, err in failures)
    _get_logger().warning("[%s] Provider chain exhausted: %s", run_id, summary)
    return ProviderChainResult(success=None, failures=failures)
