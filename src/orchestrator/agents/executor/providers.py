"""Invocation-scoped provider calls with operational fallback handling."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from orchestrator.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    circuit_breaker_for,
)
from orchestrator.clients.anthropic_client import create_anthropic_client
from orchestrator.clients.gemini_client import create_gemini_client
from orchestrator.clients.openrouter_client import create_openrouter_client
from orchestrator.provider_runtime import MODEL_CLAUDE, ProviderRuntime
from orchestrator.storage.lock import SqliteCircuitBreakerStore

from .logging import _get_logger

COST_PER_1M_INPUT_CLAUDE = 3.00
COST_PER_1M_OUTPUT_CLAUDE = 15.00
MAX_RETRIES = 1


_coord_store: SqliteCircuitBreakerStore | None = None
_cb_gemini: CircuitBreaker | None = None
_cb_openrouter: CircuitBreaker | None = None
_cb_claude: CircuitBreaker | None = None
_cb_initialized = False
_init_lock = threading.Lock()


def _init_circuit_breakers() -> None:
    """Initialize operational circuit breakers without reading credentials."""
    global _coord_store, _cb_gemini, _cb_openrouter, _cb_claude, _cb_initialized
    with _init_lock:
        if _cb_initialized:
            return
        data_dir = os.getenv("PATCHFORGE_DATA_DIR")
        _coord_store = SqliteCircuitBreakerStore(
            Path(data_dir) if data_dir is not None else Path.home() / ".patchforge"
        )
        _cb_gemini = circuit_breaker_for("gemini", store=_coord_store)
        _cb_openrouter = circuit_breaker_for("openrouter", store=_coord_store)
        _cb_claude = circuit_breaker_for("claude", store=_coord_store)
        _cb_initialized = True


def _strip_markdown(content: str) -> str:
    if content.startswith("```"):
        parts = content.split("```")
        if len(parts) >= 3:
            content = parts[1].split("\n", 1)[-1]
    return content.strip()


def _credential(runtime: ProviderRuntime, provider: str) -> str:
    credential = runtime.credentials.credential_for(provider)
    if credential is None:
        raise ValueError(f"Provider {provider} is statically ineligible")
    return credential


def _do_gemini_call(runtime: ProviderRuntime, prompt: str, run_id: str) -> tuple[str, int, int]:
    from google.genai import types

    model = runtime.model_for("gemini")
    started = time.perf_counter()
    with create_gemini_client(_credential(runtime, "gemini")) as client:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0),
        )
        usage = response.usage_metadata
        _get_logger().info("[%s] Gemini OK | latency=%.2fs", run_id, time.perf_counter() - started)
        return (
            _strip_markdown(response.text),
            usage.prompt_token_count if usage else 0,
            usage.candidates_token_count if usage else 0,
        )


def _call_gemini(runtime: ProviderRuntime, prompt: str, run_id: str) -> tuple[str, int, int]:
    _init_circuit_breakers()
    return _cb_gemini.call(lambda: _do_gemini_call(runtime, prompt, run_id))


def _do_openrouter_call(runtime: ProviderRuntime, prompt: str, run_id: str) -> tuple[str, int, int]:
    started = time.perf_counter()
    with create_openrouter_client(_credential(runtime, "openrouter")) as client:
        response = client.post(
            "/chat/completions",
            json={
                "model": runtime.model_for("openrouter"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
            },
        )
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage", {})
        _get_logger().info(
            "[%s] OpenRouter OK | latency=%.2fs", run_id, time.perf_counter() - started
        )
        return (
            _strip_markdown(data["choices"][0]["message"]["content"]),
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )


def _call_openrouter(runtime: ProviderRuntime, prompt: str, run_id: str) -> tuple[str, int, int]:
    _init_circuit_breakers()
    return _cb_openrouter.call(lambda: _do_openrouter_call(runtime, prompt, run_id))


def _do_claude_call(runtime: ProviderRuntime, prompt: str, run_id: str) -> tuple[str, int, int]:
    started = time.perf_counter()
    with create_anthropic_client(_credential(runtime, "claude")) as client:
        response = client.messages.create(
            model=runtime.model_for("claude"),
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        _get_logger().info("[%s] Claude OK | latency=%.2fs", run_id, time.perf_counter() - started)
        return (
            _strip_markdown(response.content[0].text),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )


def _call_claude(runtime: ProviderRuntime, prompt: str, run_id: str) -> tuple[str, int, int]:
    _init_circuit_breakers()
    return _cb_claude.call(lambda: _do_claude_call(runtime, prompt, run_id))


_PROVIDER_BY_NAME = {
    "gemini": _call_gemini,
    "openrouter": _call_openrouter,
    "claude": _call_claude,
}
KNOWN_PROVIDER_NAMES = tuple(sorted(_PROVIDER_BY_NAME))


def _provider_by_name() -> dict[str, object]:
    return dict(_PROVIDER_BY_NAME)


@dataclass
class ProviderChainResult:
    success: tuple[str, int, int, float | None] | None = None
    failures: list[tuple[str, str]] = field(default_factory=list)
    provider_name: str | None = None
    primary_provider_attempted: str | None = None
    primary_failure_category: str | None = None
    static_skipped_providers: tuple[str, ...] = ()


def _recoverable_exceptions() -> tuple[type[BaseException], ...]:
    if not hasattr(_recoverable_exceptions, "_cache"):
        import anthropic
        import httpx
        from google.genai.errors import APIError

        _recoverable_exceptions._cache = (
            CircuitBreakerOpenError,
            APIError,
            httpx.HTTPError,
            anthropic.APIError,
        )
    return _recoverable_exceptions._cache


def _categorize_failure(exc: BaseException) -> str:
    if isinstance(exc, CircuitBreakerOpenError):
        return "circuit_breaker_open"
    import anthropic
    import httpx
    from google.genai.errors import APIError

    if isinstance(exc, (anthropic.APIError, APIError, httpx.HTTPError)):
        message = str(exc).lower()
        if "402" in message or "credit" in message:
            return "credit_exhausted"
        if "429" in message or "rate" in message:
            return "rate_limited"
    return "other"


def _compute_cost(
    provider: object, input_tokens: int, output_tokens: int, model: str
) -> float | None:
    if provider is not _call_claude:
        return 0.0
    if model != MODEL_CLAUDE:
        _get_logger().warning("Claude model overridden to %s — cost_llm will be null", model)
        return None
    return (input_tokens / 1_000_000) * COST_PER_1M_INPUT_CLAUDE + (
        output_tokens / 1_000_000
    ) * COST_PER_1M_OUTPUT_CLAUDE


def _call_chain(
    chain: list,
    runtime: ProviderRuntime,
    prompt: str,
    run_id: str,
    on_static_skip: Callable[[str], None] | None = None,
) -> ProviderChainResult:
    """Call only statically eligible providers in the supplied policy order."""

    def _is_eligible(provider: object) -> bool:
        name = provider.__name__.removeprefix("_call_")
        return name not in runtime.credentials.eligibility or runtime.credentials.is_eligible(name)

    eligible_chain = [provider for provider in chain if _is_eligible(provider)]
    skipped = tuple(
        provider.__name__.removeprefix("_call_")
        for provider in chain
        if provider not in eligible_chain
    )
    if on_static_skip is not None:
        for provider_name in skipped:
            on_static_skip(provider_name)
    primary = eligible_chain[0].__name__.removeprefix("_call_") if eligible_chain else None
    failures: list[tuple[str, str]] = []
    primary_failure_category: str | None = None
    for provider in eligible_chain:
        provider_name = provider.__name__.removeprefix("_call_")
        try:
            if provider in _PROVIDER_BY_NAME.values():
                raw, input_tokens, output_tokens = provider(runtime, prompt, run_id)
            else:
                raw, input_tokens, output_tokens = provider(prompt, run_id)
            if not raw or not raw.strip():
                failures.append((provider.__name__, "invalid/empty response"))
                if provider_name == primary:
                    primary_failure_category = "invalid_response"
                continue
            return ProviderChainResult(
                success=(
                    raw,
                    input_tokens,
                    output_tokens,
                    _compute_cost(
                        provider, input_tokens, output_tokens, runtime.model_for(provider_name)
                    ),
                ),
                failures=failures,
                provider_name=provider_name,
                primary_provider_attempted=primary,
                primary_failure_category=primary_failure_category,
                static_skipped_providers=skipped,
            )
        except _recoverable_exceptions() as exc:
            failures.append((provider.__name__, str(exc)))
            if provider_name == primary:
                primary_failure_category = _categorize_failure(exc)
    return ProviderChainResult(
        failures=failures,
        primary_provider_attempted=primary,
        primary_failure_category=primary_failure_category,
        static_skipped_providers=skipped,
    )
