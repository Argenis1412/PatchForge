import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def log_event(
    trace_id: str,
    run_id: str,
    level: str = "info",
    source: str = "pipeline",
    stage: str | None = None,
    event: str = "",
    data: dict[str, Any] | None = None,
    logs_dir: Path | None = None,
) -> None:
    if logs_dir is None:
        logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    if data is None:
        data = {}

    entry = {
        "trace_id": trace_id,
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "source": source,
        "stage": stage,
        "event": event,
        "data": data,
    }

    path = logs_dir / "pipeline.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
