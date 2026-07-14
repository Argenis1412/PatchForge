"""Validator agent: runs linting, tests, and type-checking tools against staged changes."""

from __future__ import annotations

__all__ = [
    "run",
]

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Union

from orchestrator.circuit_breaker import circuit_breaker_for
from orchestrator.observability.logging import get_file_logger as get_file_logger
from orchestrator.schemas.validator_output import ToolResult, ValidatorOutput

from .logging import _get_logger
from .logging import _logger as _logger
from .runners import DEFAULT_TIMEOUT, run_pytest, run_ruff, run_tsc
from .summarizer import _summarize_errors

_cb_validator = circuit_breaker_for("gemini")

if TYPE_CHECKING:
    from orchestrator.schemas.config import TargetConfig


def run(
    config: Union[str, Path, "TargetConfig"] | None = None,
    staging_dir: Path | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[ValidatorOutput, dict]:
    from orchestrator.schemas.config import TargetConfig

    if config is None:
        config = TargetConfig.load(target_path=Path(".").resolve())
    elif isinstance(config, (str, Path)):
        config = TargetConfig.load(target_path=Path(config))

    from orchestrator.agents.executor.providers import init_provider_models

    init_provider_models(config)

    logs_dir = config.workspace_path / "logs"
    project_root = config.target_path.resolve()
    timeout = config.validator_timeout or DEFAULT_TIMEOUT

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    _get_logger(logs_dir).info("=== Validator run %s (timeout=%ds) ===", run_id, timeout)

    results: list[ToolResult] = []

    if progress_callback:
        progress_callback("Running ruff...")
    ruff_result = run_ruff(
        run_id,
        project_root,
        config.lint_command,
        staging_dir,
        timeout=timeout,
        ignore_dirs=config.ignore_dirs,
    )
    results.append(ruff_result)
    if ruff_result.timed_out:
        _get_logger().warning("[%s] ruff timed out — skipping remaining tools", run_id)
        if progress_callback:
            progress_callback("Skipping remaining tools (prior timeout)")
    else:
        if config.capabilities.effective_supports_tests:
            if progress_callback:
                progress_callback("Running pytest...")
            pytest_result = run_pytest(
                run_id,
                project_root,
                config.test_command,
                staging_dir,
                timeout=timeout,
                ignore_dirs=config.ignore_dirs,
            )
            results.append(pytest_result)
            if pytest_result.timed_out:
                _get_logger().warning("[%s] pytest timed out — skipping remaining tools", run_id)
                if progress_callback:
                    progress_callback("Skipping remaining tools (prior timeout)")
            elif config.capabilities.effective_supports_typecheck:
                if progress_callback:
                    progress_callback("Running tsc...")
                results.append(
                    run_tsc(
                        run_id, project_root, config.typecheck_command, staging_dir, timeout=timeout
                    )
                )
        elif config.capabilities.effective_supports_typecheck:
            if progress_callback:
                progress_callback("Running tsc...")
            tsc_result = run_tsc(
                run_id, project_root, config.typecheck_command, staging_dir, timeout=timeout
            )
            results.append(tsc_result)

    if not config.capabilities.effective_supports_tests:
        _get_logger().info("[%s] Tests skip (no framework detected or disabled)", run_id)
    if not config.capabilities.effective_supports_typecheck:
        _get_logger().info("[%s] Typecheck skip (not detected or disabled)", run_id)

    failed = [r for r in results if not r.passed]
    overall_passed = len(failed) == 0

    model_used = ""
    llm_summary: str | None = None

    tokens_input = 0
    tokens_output = 0

    if failed:
        for tool_result in failed:
            summary, _ = _summarize_errors([tool_result], run_id)
            tool_result.error_summary = summary

        llm_summary, model_used = _summarize_errors(failed, run_id)

    output = ValidatorOutput(
        overall_passed=overall_passed,
        tools=results,
        llm_summary=llm_summary,
        run_id=run_id,
        model_used_for_summary=model_used,
    )

    _get_logger().info(
        "[%s] Finished | overall=%s | failed_tools=%s",
        run_id,
        "PASS" if overall_passed else "FAIL",
        [r.tool for r in failed] or "none",
    )

    meta = {
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "cost_usd": 0.0,
        "model_used": model_used,
    }

    return output, meta


if __name__ == "__main__":
    pass
