import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class FailureType(StrEnum):
    LLM_ERROR = "llm_error"
    SCHEMA_VALIDATION_ERROR = "schema_validation_error"
    TIMEOUT_ERROR = "timeout_error"
    TOOL_ERROR = "tool_error"
    PIPELINE_ABORT = "pipeline_abort"
    UNKNOWN = "unknown"


def log_event(
    trace_id: str,
    run_id: str,
    level: str = "info",
    source: str = "pipeline",
    stage: str | None = None,
    event: str = "",
    data: dict[str, Any] | None = None,
    logs_dir: Path | None = None,
    run_dir: Path | None = None,
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

    if run_dir is not None:
        run_dir = Path(run_dir)
        if run_dir.exists():
            run_events_path = run_dir / "events.jsonl"
            with open(run_events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        else:
            import sys

            print(
                f"[log_event] WARNING: run_dir {run_dir} does not exist. "
                "Event will only be written to pipeline.jsonl.",
                file=sys.stderr,
            )


def log_failure(
    trace_id: str,
    run_id: str,
    stage: str | None,
    error_type: FailureType | str,
    message: str,
    *,
    source: str = "pipeline",
    retry_count: int | None = None,
    duration_ms: int | None = None,
    data: dict[str, Any] | None = None,
    logs_dir: Path | None = None,
    run_dir: Path | None = None,
) -> None:
    """Write a normalized failure event to pipeline.jsonl."""
    if data is None:
        data = {}

    error_type_str = error_type if isinstance(error_type, str) else error_type.value

    failure_data = {
        "error_type": error_type_str,
        "message": message,
        "retry_count": retry_count,
        "duration_ms": duration_ms,
    }

    # Ensure authority of normalized fields
    merged_data = {**data, **failure_data}

    log_event(
        trace_id=trace_id,
        run_id=run_id,
        level="error",
        source=source,
        stage=stage,
        event="failure",
        data=merged_data,
        logs_dir=logs_dir,
        run_dir=run_dir,
    )
