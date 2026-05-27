import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def log_call(
    agent: str,
    prompt: str,
    response: str,
    tokens: dict,
    cost_usd: float,
    logs_dir: Optional[Path] = None,
) -> None:
    """
    Append a structured log entry for one LLM call.

    Args:
        agent:    Name of the agent making the call (e.g. "scout").
        prompt:   The full prompt text sent to the model.
        response: The raw text response from the model.
        tokens:   Dict with at least {"input": int, "output": int}.
        cost_usd: Estimated cost in USD for the call.
        logs_dir: Optional Path directory to store the logs.
    """
    if logs_dir is None:
        logs_dir = Path("logs")
        
    logs_dir.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "prompt_chars": len(prompt),
        "response_chars": len(response),
        "tokens": tokens,
        "cost_usd": round(cost_usd, 5),
    }

    log_path = logs_dir / f"{agent}.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

