"""Task applier: builds prompts, calls provider chains, generates diffs, writes staging files."""

from __future__ import annotations

from pathlib import Path

from orchestrator.schemas.architect_output import Task
from orchestrator.schemas.executor_output import FileChange, TaskStatus

from .diffing import _make_diff
from .logging import _get_logger
from .providers import _PROVIDER_CHAIN, MAX_RETRIES, _call_chain, _provider_by_name


def _build_prompt(task: Task, file_path: Path, file_content: str) -> str:
    return f"""You are a precise code editor. Apply exactly one change to the file below.

TASK
----
Title       : {task.title}
Description : {task.description}
File        : {file_path}

RULES (mandatory)
-----------------
1. Return ONLY the complete modified file content.
2. Do NOT include markdown code fences (``` or ~~~).
3. Do NOT include any explanation, comments, or preamble.
4. Do NOT change anything outside the scope of the task.
5. If the change is already applied, return the file as-is.

FILE CONTENT
------------
{file_content}
"""


def _apply_task(
    task: Task,
    run_id: str,
    project_root: Path,
    staging_dir: Path,
    force_provider: str | None = None,
) -> FileChange:
    if not task.files_to_modify:
        _get_logger().warning("[%s] Task %s has no files_to_modify — skip", run_id, task.task_id)
        return FileChange(
            task_id=task.task_id, file="", status="error", error="files_to_modify is empty"
        )

    relative_path = task.files_to_modify[0]

    from orchestrator.exceptions import PathSafetyError  # lazy (file convention)
    from orchestrator.safety import ensure_safe_relative  # lazy (file convention)

    try:
        ensure_safe_relative(relative_path, project_root)
    except ValueError as exc:
        raise PathSafetyError(path=relative_path, base=project_root) from exc

    file_path = project_root / relative_path

    if not file_path.exists():
        msg = f"File not found: {file_path}"
        _get_logger().error("[%s] %s", run_id, msg)
        return FileChange(task_id=task.task_id, file=relative_path, status="error", error=msg)

    staged_path = staging_dir / relative_path
    if staged_path.exists():
        original_content = staged_path.read_text(encoding="utf-8")
    else:
        original_content = file_path.read_text(encoding="utf-8")
    prompt = _build_prompt(task, file_path, original_content)

    modified_content: str | None = None
    input_tokens = output_tokens = 0
    cost_this_call = 0.0

    # force_provider is orthogonal to risk_level: it only changes which LLM
    # generates the patch.  risk_level still controls high-risk gating
    # (pending_human_review) and staging writes below.
    if force_provider:
        by_name = _provider_by_name()
        provider = by_name.get(force_provider)
        if provider is None:
            raise ValueError(
                f"Unknown provider: {force_provider}. Available: {tuple(sorted(by_name))}"
            )
        chain = [provider]
    else:
        chain = _PROVIDER_CHAIN.get(task.risk_level)
    if not chain:
        raise ValueError(f"Unknown risk level: {task.risk_level}")

    last_failures: list[tuple[str, str]] = []
    for attempt in range(MAX_RETRIES + 1):
        chain_result = _call_chain(chain, prompt, run_id)
        if chain_result.success is not None:
            raw, input_tokens, output_tokens, cost_this_call = chain_result.success
            modified_content = raw
            break
        last_failures = chain_result.failures
        _get_logger().warning(
            "[%s] Attempt %d/%d: all providers failed for %s-risk task",
            run_id,
            attempt + 1,
            MAX_RETRIES + 1,
            task.risk_level,
        )
    else:
        failure_summary = "; ".join(f"{name}→{err}" for name, err in last_failures)
        _get_logger().error(
            "[%s] All providers exhausted for task %s: %s",
            run_id,
            task.task_id,
            failure_summary,
        )
        return FileChange(
            task_id=task.task_id,
            file=relative_path,
            status="error",
            error=f"All providers failed for {task.risk_level}-risk task: {failure_summary}",
        )

    assert modified_content is not None

    if original_content and not modified_content.endswith(original_content[-1]):
        modified_content += original_content[-1]

    diff = _make_diff(original_content, modified_content, relative_path)

    if not diff:
        _get_logger().info("[%s] Task %s — no changes (idempotent)", run_id, task.task_id)
        return FileChange(
            task_id=task.task_id,
            file=relative_path,
            status=TaskStatus.NOOP,
            diff=None,
            original_content=original_content,
            modified_content=original_content,
            tokens_used=input_tokens + output_tokens,
            cost_usd=cost_this_call,
        )

    if task.risk_level == "high":
        _get_logger().info(
            "[%s] Task %s — diff generated (HIGH risk, not written)", run_id, task.task_id
        )
        return FileChange(
            task_id=task.task_id,
            file=relative_path,
            status="pending_human_review",
            diff=diff,
            original_content=original_content,
            modified_content=modified_content,
            tokens_used=input_tokens + output_tokens,
            cost_usd=cost_this_call,
        )
    else:
        staging_path = staging_dir / relative_path
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        staging_path.write_text(modified_content, encoding="utf-8")
        _get_logger().info(
            "[%s] Task %s — applied to staging: %s", run_id, task.task_id, staging_path
        )
        return FileChange(
            task_id=task.task_id,
            file=relative_path,
            status="applied",
            diff=diff,
            original_content=original_content,
            modified_content=modified_content,
            tokens_used=input_tokens + output_tokens,
            cost_usd=cost_this_call,
        )
