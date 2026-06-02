import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def log_call(
    agent: str,
    prompt: str,
    response: str,
    tokens: dict,
    cost_usd: float,
    logs_dir: Optional[Path] = None,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    stage: str | None = None,
    span_id: str | None = None,
    model: str | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
) -> None:
    if logs_dir is None:
        logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Legacy per-agent log file (backward compatible)
    legacy = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "prompt_chars": len(prompt),
        "response_chars": len(response),
        "tokens": tokens,
        "cost_usd": round(cost_usd, 5),
    }
    log_path = logs_dir / f"{agent}.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(legacy) + "\n")

    # New unified llm_calls.jsonl
    data: dict[str, Any] = {
        "agent": agent,
        "prompt_chars": len(prompt),
        "response_chars": len(response),
        "tokens": tokens,
        "cost_usd": round(cost_usd, 5),
    }
    if model is not None:
        data["model"] = model
    if latency_ms is not None:
        data["latency_ms"] = latency_ms
    if error is not None:
        data["error"] = error

    entry = {
        "trace_id": trace_id,
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "error" if error else "info",
        "source": "agent",
        "stage": stage,
        "span_id": span_id,
        "event": "llm_call",
        "data": data,
    }
    llm_path = logs_dir / "llm_calls.jsonl"
    with open(llm_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
