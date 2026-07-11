"""Architect agent: generates implementation plans from Scout output or issue files."""

__all__ = [
    "run",
    "run_from_issue",
]

import json
import sys
from pathlib import Path
from typing import Optional, Union

from orchestrator.agents.architect.file_collector import build_target_files_block
from orchestrator.agents.architect.prompts import ARCHITECT_PROMPT, ISSUE_ARCHITECT_PROMPT
from orchestrator.agents.architect.provider import MODEL, call_claude
from orchestrator.llm.parser import LLMParseError, SchemaValidationError, parse_llm_response
from orchestrator.observability.events import FailureType, log_failure
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.issue import IssueInput
from orchestrator.schemas.scout_output import ScoutOutput


def run(
    scout_output: ScoutOutput,
    config: Optional[Union[str, Path, TargetConfig]] = None,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    force_provider: str | None = None,
) -> tuple[ArchitectOutput, dict]:
    logs_dir: Optional[Path] = None
    if config is not None:
        if isinstance(config, (str, Path)):
            config = TargetConfig.load(target_path=Path(config))
        logs_dir = config.workspace_path / "logs"

    print("[Architect] Processing ScoutOutput object...")
    scout_data = scout_output.model_dump_json()

    target_files_block, paths, truncated, total = build_target_files_block(config)
    print(
        f"[Architect] Target files: {len(paths)} of {total} paths injected (truncated={truncated})"
    )
    display_model = force_provider or MODEL
    print(f"[Architect] Asking {display_model} to structure the implementation plan...")

    raw_response, tokens, cost, model_used = call_claude(
        ARCHITECT_PROMPT.format(scout_data=scout_data, target_files=target_files_block),
        orchestratorel="architect",
        logs_dir=logs_dir,
        trace_id=trace_id,
        run_id=run_id,
        stage="architect",
        span_id="architect",
        force_provider=force_provider,
    )

    print(f"[Architect] Done | model={model_used} | tokens: {tokens} | cost: ${cost:.5f}")

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
        "model_used": model_used,
    }

    return output, meta


def run_from_issue(
    issue_input: IssueInput,
    config: Optional[Union[str, Path, TargetConfig]] = None,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    force_provider: str | None = None,
) -> tuple[ArchitectOutput, dict]:
    """Run the Architect agent from a human-written issue file.

    Uses :data:`ISSUE_ARCHITECT_PROMPT` instead of the Scout-based prompt.
    Returns the same ``(ArchitectOutput, meta)`` tuple as :func:`run`.
    """
    logs_dir: Optional[Path] = None
    if config is not None:
        if isinstance(config, (str, Path)):
            config = TargetConfig.load(target_path=Path(config))
        logs_dir = config.workspace_path / "logs"

    print("[Architect] Processing IssueInput object...")

    target_files_block, paths, truncated, total = build_target_files_block(config)
    print(
        f"[Architect] Target files: {len(paths)} of {total} paths injected (truncated={truncated})"
    )

    def _escape(v: str) -> str:
        return v.replace("{", "{{").replace("}", "}}")

    issue_data = ISSUE_ARCHITECT_PROMPT.format(
        title=_escape(issue_input.title),
        severity=_escape(issue_input.severity),
        labels=_escape(", ".join(issue_input.labels)),
        body=_escape(issue_input.body),
        target_files=_escape(target_files_block),
    )
    display_model = force_provider or MODEL
    print(f"[Architect] Asking {display_model} to structure the implementation plan...")

    raw_response, tokens, cost, model_used = call_claude(
        issue_data,
        orchestratorel="architect",
        logs_dir=logs_dir,
        trace_id=trace_id,
        run_id=run_id,
        stage="architect",
        span_id="architect-issue",
        force_provider=force_provider,
    )

    print(f"[Architect] Done | model={model_used} | tokens: {tokens} | cost: ${cost:.5f}")

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
        "model_used": model_used,
    }

    return output, meta


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m orchestrator.agents.architect <path_to_scout_output.json>")
        sys.exit(1)

    scout_path = sys.argv[1]
    with open(scout_path, "r") as f:
        scout_data = ScoutOutput.model_validate_json(f.read())

    result, _ = run(scout_data)
    print("\n-- Architect Output --")
    print(json.dumps(result.model_dump(), indent=2))
