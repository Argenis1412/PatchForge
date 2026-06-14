"""
agents/executor.py

Executor — third agent in the pipeline.

Routing by risk_level:
  LOW    → Gemini Flash applies the change
  MEDIUM → Groq (Llama 3) applies the change
  HIGH   → Claude Sonnet generates the diff, but does NOT write (pending_human_review)

Contract:
  Input  : ArchitectOutput (from JSON file or directly)
  Output : ExecutorOutput  (applied changes + diff + cost)
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from orchestrator.circuit_breaker import CircuitBreakerOpenError, circuit_breaker_for
from orchestrator.clients.anthropic_client import get_anthropic_client
from orchestrator.clients.gemini_client import get_gemini_client
from orchestrator.clients.groq_client import get_groq_client
from orchestrator.exceptions import CycleDetectedError, SchedulerInvariantError
from orchestrator.observability.logging import get_file_logger
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.executor_output import ExecutorOutput, FileChange, TaskStatus

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
    "low": [],  # defined below after the _call_* functions
    "medium": [],
    "high": [],
}

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parent.parent.parent)))

LOGS_DIR = Path(__file__).parent.parent / "logs"

# ---------------------------------------------------------------------------
# Shared circuit breakers per provider (process-wide via registry)
# ---------------------------------------------------------------------------

_cb_gemini = circuit_breaker_for("gemini")
_cb_groq = circuit_breaker_for("groq")
_cb_claude = circuit_breaker_for("claude")

# ---------------------------------------------------------------------------
# Logger (lazy-initialized)
# ---------------------------------------------------------------------------

_logger = None


def _get_logger(logs_dir: Optional[Path] = None):
    global _logger
    if logs_dir is not None or _logger is None:
        _logger = get_file_logger("executor", logs_dir, "executor.log")
        logging.getLogger("httpx").setLevel(logging.WARNING)
    return _logger


# ---------------------------------------------------------------------------
# Model Helpers
# ---------------------------------------------------------------------------


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


def _recoverable_exceptions() -> tuple:
    """Lazy-init tuple of recoverable provider exceptions.

    Not a module-level constant because it imports SDK exception classes
    (google.genai.errors, httpx, anthropic) that should remain loaded
    on demand, consistent with the project's lazy-import convention.
    First call triggers imports; subsequent calls return the cached tuple.
    """
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
    """Try each provider in *chain*; return first valid response or *None*."""
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


# ---------------------------------------------------------------------------
# Core: apply task
# ---------------------------------------------------------------------------


def _apply_task(task: Task, run_id: str, project_root: Path, staging_dir: Path) -> FileChange:
    if not task.files_to_modify:
        _get_logger().warning("[%s] Task %s has no files_to_modify — skip", run_id, task.task_id)
        return FileChange(
            task_id=task.task_id, file="", status="error", error="files_to_modify is empty"
        )

    relative_path = task.files_to_modify[0]

    from orchestrator.exceptions import PathSafetyError  # lazy (file convention)
    from orchestrator.safety import ensure_safe_relative  # lazy (file convention)

    # SAFETY: MUST remain outside the provider chain loop.
    # If placed inside, any exception from ensure_safe_relative could be
    # misinterpreted as a recoverable provider failure.
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

    chain = _PROVIDER_CHAIN.get(task.risk_level)
    if not chain:
        raise ValueError(f"Unknown risk level: {task.risk_level}")

    for attempt in range(MAX_RETRIES + 1):
        result = _call_chain(chain, prompt, run_id)
        if result is not None:
            raw, input_tokens, output_tokens, cost_this_call = result
            modified_content = raw
            break
        _get_logger().warning(
            "[%s] Attempt %d/%d: all providers failed for %s-risk task",
            run_id,
            attempt + 1,
            MAX_RETRIES + 1,
            task.risk_level,
        )
    else:
        return FileChange(
            task_id=task.task_id,
            file=relative_path,
            status="error",
            error=f"All providers failed for {task.risk_level}-risk task",
        )

    assert modified_content is not None

    # Ensure trailing newline matches the original to avoid no-op hunks
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


def _make_diff(original: str, modified: str, filename: str) -> str:
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
    )
    return "".join(diff_lines)


def rollback_to_commit(repo_root: Path, target_sha: str) -> None:
    """Reset working tree to target_sha, raising RollbackError on failure.

    Uses ``git reset --hard <target_sha>`` followed by ``git clean -fd``.
    Raises RollbackError if either command fails — the caller must not
    proceed if this raises.
    """
    from orchestrator.exceptions import RollbackError
    from orchestrator.git import force_reset_apply

    result = force_reset_apply(repo_root, target_sha)
    if result.return_code != 0:
        raise RollbackError(
            repo_root=repo_root,
            target_sha=target_sha,
            stderr=result.stderr,
        )


# ---------------------------------------------------------------------------
# DAG Scheduler: build + topological order
# ---------------------------------------------------------------------------


def _build_dag(tasks: list[Task]) -> dict[str, set[str]]:
    """Map task_id -> set(dependency ids). Validate all deps exist."""
    known_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for task in tasks:
        if task.task_id in known_ids:
            duplicate_ids.add(task.task_id)
        known_ids.add(task.task_id)
    if duplicate_ids:
        dup_list = ", ".join(sorted(duplicate_ids))
        raise SchedulerInvariantError(f"Duplicate task_id(s) in plan: {dup_list}")
    dag: dict[str, set[str]] = {}
    for task in tasks:
        for dep in task.dependencies:
            if dep not in known_ids:
                raise SchedulerInvariantError(
                    f"Task {task.task_id} depends on {dep}, but {dep} does not exist in the plan"
                )
        dag[task.task_id] = set(task.dependencies)
    return dag


def _topological_order(tasks: list[Task], dag: dict[str, set[str]]) -> list[Task]:
    """Kahn's algorithm - O(V^2) scan for determinism.

    Scans tasks in declaration order at each round, picks the first whose
    dependencies are all resolved. Raises CycleDetectedError if no candidate
    can be found (cycle).
    """
    task_map = {t.task_id: t for t in tasks}
    remaining = set(dag.keys())
    resolved: set[str] = set()
    order: list[Task] = []

    while remaining:
        candidates = [
            tid
            for tid in (t.task_id for t in tasks)
            if tid in remaining and not (dag[tid] - resolved)
        ]
        if not candidates:
            raise CycleDetectedError(list(remaining))
        chosen = candidates[0]
        order.append(task_map[chosen])
        resolved.add(chosen)
        remaining.remove(chosen)

    return order


# ---------------------------------------------------------------------------
# Public Entrypoint
# ---------------------------------------------------------------------------


def run(
    architect_output: ArchitectOutput,
    config: Optional[Union[str, Path, TargetConfig]] = None,
    staging_dir: Optional[Path] = None,
) -> tuple[ExecutorOutput, dict]:
    if config is None:
        config = TargetConfig.load(target_path=PROJECT_ROOT)
    elif isinstance(config, (str, Path)):
        config = TargetConfig.load(target_path=Path(config))

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    logs_dir = config.workspace_path / "logs"
    project_root = config.target_path.resolve()
    if staging_dir is None:
        staging_dir = config.workspace_path / "outputs" / "staging" / run_id

    # Initialize logger
    _get_logger(logs_dir)
    _get_logger().info("=== Executor run %s ===", run_id)

    model_string = f"GM:{MODEL_GEMINI}|GQ:{MODEL_GROQ}|CL:{MODEL_CLAUDE}"
    result = ExecutorOutput(model=model_string, run_id=run_id)

    total_tokens_input = 0
    total_tokens_output = 0

    tasks = architect_output.implementation_plan
    dag = _build_dag(tasks)
    ordered_tasks = _topological_order(tasks, dag)
    task_status_results: dict[str, TaskStatus] = {}

    for task in ordered_tasks:
        _get_logger().info(
            "[%s] Task %s | risk=%s | title=%s", run_id, task.task_id, task.risk_level, task.title
        )

        # --- dependency check ---
        skip = False
        for dep_id in task.dependencies:
            if dep_id not in task_status_results:
                raise SchedulerInvariantError(
                    f"Task {task.task_id} depends on {dep_id}, but {dep_id} was never scheduled"
                )
            dep_status = task_status_results[dep_id]
            if dep_status in {TaskStatus.ERROR, TaskStatus.SKIPPED, TaskStatus.PENDING_REVIEW}:
                _get_logger().info(
                    "[%s] Task %s — SKIPPED (dependency %s has status %s)",
                    run_id,
                    task.task_id,
                    dep_id,
                    dep_status,
                )
                result.errors.append(
                    FileChange(
                        task_id=task.task_id,
                        file=task.files_to_modify[0] if task.files_to_modify else "",
                        status=TaskStatus.SKIPPED,
                        diff=None,
                        original_content=None,
                        modified_content=None,
                        error=f"dependency {dep_id} has status {dep_status}",
                        tokens_used=0,
                        cost_usd=0.0,
                    )
                )
                task_status_results[task.task_id] = TaskStatus.SKIPPED
                skip = True
                break

        if skip:
            continue

        # --- execute task (all dependencies satisfied) ---
        task_statuses: list[TaskStatus] = []
        for file_relative in task.files_to_modify:
            single_file_task = task.model_copy(update={"files_to_modify": [file_relative]})
            change = _apply_task(single_file_task, run_id, project_root, staging_dir)

            result.total_tokens += change.tokens_used
            result.total_cost_usd += change.cost_usd

            # Simple heuristic for token tracking: _apply_task returns combined tokens_used
            # It does not separate input/output tokens, so estimate evenly for meta purposes.
            total_tokens_input += change.tokens_used // 2
            total_tokens_output += change.tokens_used // 2

            # Route per-file change by status
            if change.status in {TaskStatus.APPLIED, TaskStatus.NOOP}:
                result.applied.append(change)
            elif change.status == TaskStatus.PENDING_REVIEW:
                result.pending_review.append(change)
            else:
                result.errors.append(change)

            task_statuses.append(change.status)

        # Aggregate: worst status wins for dependency tracking
        if TaskStatus.ERROR in task_statuses:
            task_status_results[task.task_id] = TaskStatus.ERROR
        elif TaskStatus.PENDING_REVIEW in task_statuses:
            task_status_results[task.task_id] = TaskStatus.PENDING_REVIEW
        elif TaskStatus.APPLIED in task_statuses:
            task_status_results[task.task_id] = TaskStatus.APPLIED
        else:
            task_status_results[task.task_id] = TaskStatus.NOOP

    _get_logger().info(
        "[%s] Finished | applied=%d | pending_review=%d | errors=%d | cost=$%.6f",
        run_id,
        len(result.applied),
        len(result.pending_review),
        len(result.errors),
        result.total_cost_usd,
    )

    meta = {
        "tokens_input": total_tokens_input,
        "tokens_output": total_tokens_output,
        "cost_usd": result.total_cost_usd,
        "model_used": model_string,
    }

    return result, meta


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agents/executor.py <architect_output.json>")
        sys.exit(1)

    architect_json_path = Path(sys.argv[1])
    if not architect_json_path.exists():
        print(f"File not found: {architect_json_path}")
        sys.exit(1)

    architect_data = json.loads(architect_json_path.read_text(encoding="utf-8"))
    architect_output = ArchitectOutput.model_validate(architect_data)

    result, _ = run(architect_output)

    print(f"\n[OK] Applied       : {len(result.applied)}")
    print(f"[~] Pending review : {len(result.pending_review)}")
    print(f"[X] Errors         : {len(result.errors)}")
    print(f"[$] Total cost     : ${result.total_cost_usd:.6f}")

    if result.applied:
        print("\n--- Applied diffs ---")
        for change in result.applied:
            print(f"\n[{change.task_id}] {change.file}")
            print(change.diff)

    if result.pending_review:
        print("\n--- PENDING diffs (HIGH risk, not written) ---")
        for change in result.pending_review:
            print(f"\n[{change.task_id}] {change.file}")
            print(change.diff)
