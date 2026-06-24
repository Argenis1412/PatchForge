"""Multi-provider LLM chain (Gemini, Groq, Claude) with circuit breakers and fallback."""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable

from orchestrator.circuit_breaker import (
    RECOVERY_BACKOFF,
    CircuitBreakerOpenError,
    CircuitBreakerState,
    circuit_breaker_for,
)
from orchestrator.clients.anthropic_client import get_anthropic_client
from orchestrator.clients.gemini_client import get_gemini_client
from orchestrator.clients.groq_client import get_groq_client

from .logging import _get_logger


class ProbeSlotBusyError(CircuitBreakerOpenError):
    """HALF_OPEN probe slot is held by another worker — contention, not a provider failure."""

    def __init__(self, provider: str) -> None:
        super().__init__(provider, None, 0.0, "probe slot busy — another worker holds the token")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL_GEMINI = "gemini-2.5-flash"
MODEL_GROQ = "llama-3.3-70b-versatile"
MODEL_CLAUDE = "claude-sonnet-4-6"

COST_PER_1M_INPUT_CLAUDE = 3.00
COST_PER_1M_OUTPUT_CLAUDE = 15.00

TIMEOUT_SECONDS = 60
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
# Shared circuit breakers per provider (process-wide via registry)
# ---------------------------------------------------------------------------

_cb_gemini = circuit_breaker_for("gemini")
_cb_groq = circuit_breaker_for("groq")
_cb_claude = circuit_breaker_for("claude")

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
    return _cb_gemini.call(lambda: _do_gemini_call(prompt, run_id))


def _do_groq_call(prompt: str, run_id: str) -> tuple[str, int, int]:
    log = _get_logger()
    client = get_groq_client()
    headers = {
        "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL_GROQ,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }

    log.debug("[%s] Groq request | model=%s | prompt_chars=%d", run_id, MODEL_GROQ, len(prompt))

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
        "[%s] Groq OK | latency=%.2fs | in=%d | out=%d",
        run_id,
        elapsed,
        input_tokens,
        output_tokens,
    )

    return content, input_tokens, output_tokens


def _call_groq(prompt: str, run_id: str) -> tuple[str, int, int]:
    return _cb_groq.call(lambda: _do_groq_call(prompt, run_id))


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
    return _cb_claude.call(lambda: _do_claude_call(prompt, run_id))


# ---------------------------------------------------------------------------
# Provider fallback chain (populated after all _call_* defs)
# ---------------------------------------------------------------------------

_PROVIDER_CHAIN["low"] = [_call_gemini, _call_groq, _call_claude]
_PROVIDER_CHAIN["medium"] = [_call_groq, _call_gemini, _call_claude]
_PROVIDER_CHAIN["high"] = [_call_claude]


def _call_with_half_open_probe(
    conn_coord: sqlite3.Connection,
    provider_name: str,
    fn: Callable[..., object],
    *args: object,
) -> object:
    """Reactive HALF_OPEN probe using a SQLite-backed token.

    CLOSED → call fn directly.
    OPEN (timeout not elapsed) → fast-reject with CircuitBreakerOpenError.
    OPEN (timeout elapsed) → transition to HALF_OPEN under lock, fall through.
    HALF_OPEN (slot held) → raise ProbeSlotBusyError (not counted as failure).
    HALF_OPEN (slot free) → acquire token, call fn, persist result, release token.
    """
    row = conn_coord.execute(
        "SELECT state, last_failure_at, recovery_timeout, failures "
        "FROM cb_state WHERE provider = ?",
        (provider_name,),
    ).fetchone()

    current_state = row["state"] if row else CircuitBreakerState.CLOSED.value

    if current_state == CircuitBreakerState.CLOSED.value:
        return fn(*args)

    if current_state == CircuitBreakerState.OPEN.value:
        last_failure = row["last_failure_at"] or 0.0
        recovery_timeout = row["recovery_timeout"] or 60.0
        retry_after = last_failure + recovery_timeout

        if time.monotonic() < retry_after:
            raise CircuitBreakerOpenError(provider_name, CircuitBreakerState.OPEN, retry_after)

        # Timeout expired — transition to HALF_OPEN under lock.
        conn_coord.execute("BEGIN IMMEDIATE")
        try:
            fresh = conn_coord.execute(
                "SELECT state FROM cb_state WHERE provider = ?", (provider_name,)
            ).fetchone()
            if fresh and fresh["state"] == CircuitBreakerState.OPEN.value:
                conn_coord.execute(
                    "UPDATE cb_state SET state = ? WHERE provider = ?",
                    (CircuitBreakerState.HALF_OPEN.value, provider_name),
                )
            conn_coord.execute("COMMIT")
        except Exception:
            conn_coord.execute("ROLLBACK")
            raise
        current_state = CircuitBreakerState.HALF_OPEN.value

    # HALF_OPEN: acquire probe token.
    conn_coord.execute("BEGIN IMMEDIATE")
    try:
        token = conn_coord.execute(
            "SELECT 1 FROM half_open_probe WHERE provider = ?", (provider_name,)
        ).fetchone()
        if token:
            conn_coord.execute("ROLLBACK")
            raise ProbeSlotBusyError(provider_name)
        conn_coord.execute(
            "INSERT INTO half_open_probe (provider, worker_id, acquired_at) "
            "VALUES (?, ?, datetime('now'))",
            (provider_name, os.environ.get("WORKER_ID", "unknown")),
        )
        conn_coord.execute("COMMIT")
    except ProbeSlotBusyError:
        raise
    except Exception:
        conn_coord.execute("ROLLBACK")
        raise

    try:
        result = fn(*args)
        # Success: reset to CLOSED.
        conn_coord.execute(
            "INSERT OR REPLACE INTO cb_state "
            "(provider, state, failures, last_failure_at, recovery_timeout) "
            "VALUES (?, ?, 0, 0.0, 60.0)",
            (provider_name, CircuitBreakerState.CLOSED.value),
        )
        return result
    except Exception as exc:
        # Failure: transition to OPEN with exponential backoff.
        curr = conn_coord.execute(
            "SELECT failures FROM cb_state WHERE provider = ?", (provider_name,)
        ).fetchone()
        failures = (curr["failures"] if curr else 0) + 1
        threshold = 3
        backoff_index = min((failures - 1) // threshold, len(RECOVERY_BACKOFF) - 1)
        new_timeout = RECOVERY_BACKOFF[backoff_index]
        conn_coord.execute(
            "INSERT OR REPLACE INTO cb_state "
            "(provider, state, failures, last_failure_at, recovery_timeout) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                provider_name,
                CircuitBreakerState.OPEN.value,
                failures,
                time.monotonic(),
                new_timeout,
            ),
        )
        raise exc
    finally:
        _release_probe_token(conn_coord, provider_name)


def _release_probe_token(
    conn_coord: sqlite3.Connection, provider: str | None = None
) -> None:
    if provider:
        conn_coord.execute(
            "DELETE FROM half_open_probe WHERE provider = ?", (provider,)
        )
    else:
        worker = os.environ.get("WORKER_ID", "unknown")
        conn_coord.execute(
            "DELETE FROM half_open_probe WHERE worker_id = ?", (worker,)
        )


def _cleanup_stale_probes(conn_coord: sqlite3.Connection) -> None:
    conn_coord.execute(
        "DELETE FROM half_open_probe WHERE acquired_at < datetime('now', '-5 minutes')"
    )


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


def _call_chain(chain: list, prompt: str, run_id: str) -> tuple[str, int, int, float] | None:
    for provider in chain:
        try:
            raw, input_tokens, output_tokens = provider(prompt, run_id)
            if not _is_valid_provider_response(raw):
                _get_logger().warning(
                    "[%s] Invalid/empty response from %s, trying next",
                    run_id,
                    provider.__name__,
                )
                continue
            cost = _compute_cost(provider, input_tokens, output_tokens)
            return raw, input_tokens, output_tokens, cost
        except _recoverable_exceptions() as exc:
            _get_logger().info(
                "[%s] %s unavailable: %s, trying next",
                run_id,
                provider.__name__,
                exc,
            )
            continue
    return None
