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

from orchestrator.clients.anthropic_client import get_anthropic_client
from orchestrator.clients.gemini_client import get_gemini_client
from orchestrator.clients.groq_client import get_groq_client
from orchestrator.observability.logging import get_file_logger
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.executor_output import ExecutorOutput, FileChange

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

PROJECT_ROOT = Path(
    os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parent.parent.parent))
)

LOGS_DIR = Path(__file__).parent.parent / "logs"

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

def _call_gemini(prompt: str, run_id: str) -> tuple[str, int, int]:
    from google.genai import types
    client = get_gemini_client()
    log = _get_logger()
    log.debug("[%s] Gemini request | model=%s | prompt_chars=%d",
                 run_id, MODEL_GEMINI, len(prompt))

    t0 = time.perf_counter()
    response = client.models.generate_content(
        model=MODEL_GEMINI,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.0)
    )
    elapsed = time.perf_counter() - t0

    content = _strip_markdown(response.text)

    usage = response.usage_metadata
    input_tokens = usage.prompt_token_count if usage else 0
    output_tokens = usage.candidates_token_count if usage else 0

    log.info("[%s] Gemini OK | latency=%.2fs | in=%d | out=%d",
                run_id, elapsed, input_tokens, output_tokens)

    return content, input_tokens, output_tokens


def _call_groq(prompt: str, run_id: str) -> tuple[str, int, int]:
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

    log.debug("[%s] Groq request | model=%s | prompt_chars=%d",
                 run_id, MODEL_GROQ, len(prompt))

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

    log.info("[%s] Groq OK | latency=%.2fs | in=%d | out=%d",
                run_id, elapsed, input_tokens, output_tokens)

    return content, input_tokens, output_tokens


def _call_claude(prompt: str, run_id: str) -> tuple[str, int, int]:
    client = get_anthropic_client()
    log = _get_logger()
    log.debug("[%s] Claude request | model=%s | prompt_chars=%d",
                 run_id, MODEL_CLAUDE, len(prompt))

    t0 = time.perf_counter()
    response = client.messages.create(
        model=MODEL_CLAUDE,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0
    )
    elapsed = time.perf_counter() - t0

    content = _strip_markdown(response.content[0].text)

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    log.info("[%s] Claude OK | latency=%.2fs | in=%d | out=%d",
                run_id, elapsed, input_tokens, output_tokens)

    return content, input_tokens, output_tokens


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
    file_path = project_root / relative_path

    if not file_path.exists():
        msg = f"File not found: {file_path}"
        _get_logger().error("[%s] %s", run_id, msg)
        return FileChange(
            task_id=task.task_id, file=relative_path, status="error", error=msg
        )

    original_content = file_path.read_text(encoding="utf-8")
    prompt = _build_prompt(task, file_path, original_content)

    modified_content: str | None = None
    input_tokens = output_tokens = 0
    cost_this_call = 0.0

    for attempt in range(MAX_RETRIES + 1):
        try:
            if task.risk_level == "low":
                raw, input_tokens, output_tokens = _call_gemini(prompt, run_id)
                cost_this_call = 0.0
            elif task.risk_level == "medium":
                raw, input_tokens, output_tokens = _call_groq(prompt, run_id)
                cost_this_call = 0.0
            elif task.risk_level == "high":
                raw, input_tokens, output_tokens = _call_claude(prompt, run_id)
                cost_this_call = (input_tokens / 1_000_000) * COST_PER_1M_INPUT_CLAUDE + (output_tokens / 1_000_000) * COST_PER_1M_OUTPUT_CLAUDE
            else:
                raise ValueError(f"Unknown risk level: {task.risk_level}")

            if not raw:
                raise ValueError("Empty model response")

            modified_content = raw
            break

        except (ValueError, Exception) as exc:
            log = _get_logger()
            log.warning("[%s] Attempt %d/%d failed for task %s: %s",
                           run_id, attempt + 1, MAX_RETRIES + 1, task.task_id, exc)
            if attempt == MAX_RETRIES:
                return FileChange(
                    task_id=task.task_id,
                    file=relative_path,
                    status="error",
                    error=str(exc),
                    tokens_used=input_tokens + output_tokens,
                    cost_usd=cost_this_call,
                )

    assert modified_content is not None

    diff = _make_diff(original_content, modified_content, relative_path)

    if not diff:
        _get_logger().info("[%s] Task %s — no changes (idempotent)", run_id, task.task_id)
        return FileChange(
            task_id=task.task_id, file=relative_path, status="applied",
            diff="(no changes — already applied)", original_content=original_content, modified_content=original_content,
            tokens_used=input_tokens + output_tokens, cost_usd=cost_this_call
        )

    if task.risk_level == "high":
        _get_logger().info("[%s] Task %s — diff generated (HIGH risk, not written)", run_id, task.task_id)
        return FileChange(
            task_id=task.task_id, file=relative_path, status="pending_human_review",
            diff=diff, original_content=original_content, modified_content=modified_content,
            tokens_used=input_tokens + output_tokens, cost_usd=cost_this_call
        )
    else:
        staging_path = staging_dir / relative_path
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        staging_path.write_text(modified_content, encoding="utf-8")
        _get_logger().info("[%s] Task %s — applied to staging: %s", run_id, task.task_id, staging_path)
        return FileChange(
            task_id=task.task_id, file=relative_path, status="applied",
            diff=diff, original_content=original_content, modified_content=modified_content,
            tokens_used=input_tokens + output_tokens, cost_usd=cost_this_call
        )

def _make_diff(original: str, modified: str, filename: str) -> str:
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            original_lines, modified_lines,
            fromfile=f"a/{filename}", tofile=f"b/{filename}",
            lineterm="",
        )
    )
    return "".join(diff_lines)

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

    for task in architect_output.implementation_plan:
        _get_logger().info("[%s] Task %s | risk=%s | title=%s",
                    run_id, task.task_id, task.risk_level, task.title)

        for file_relative in task.files_to_modify:
            single_file_task = task.model_copy(update={"files_to_modify": [file_relative]})
            change = _apply_task(single_file_task, run_id, project_root, staging_dir)

            result.total_tokens += change.tokens_used
            result.total_cost_usd += change.cost_usd

            # Simple heuristic for token tracking: _apply_task returns combined tokens_used
            # It does not separate input/output tokens, so estimate evenly for meta purposes.
            total_tokens_input += change.tokens_used // 2
            total_tokens_output += change.tokens_used // 2

            if change.status == "applied":
                result.applied.append(change)
            elif change.status == "pending_human_review":
                result.pending_review.append(change)
            else:
                result.errors.append(change)

    _get_logger().info(
        "[%s] Finalizado | applied=%d | pending_review=%d | errors=%d | cost=$%.6f",
        run_id, len(result.applied), len(result.pending_review), len(result.errors),
        result.total_cost_usd
    )

    meta = {
        "tokens_input": total_tokens_input,
        "tokens_output": total_tokens_output,
        "cost_usd": result.total_cost_usd,
        "model_used": model_string
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
