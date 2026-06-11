# agents/scout.py
import json
import os
import sys
import time
from pathlib import Path
from typing import Union

from orchestrator.clients.gemini_client import get_gemini_client
from orchestrator.exceptions import ProviderError
from orchestrator.observability.events import FailureType, log_failure
from orchestrator.observability.logger import log_call
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.scout_output import ScoutOutput

MODEL = "gemini-2.5-flash"

COST_PER_1M_INPUT = 0.075
COST_PER_1M_OUTPUT = 0.30

# ── helpers ────────────────────────────────────────────────────────────────


def read_file_tree(root: Path, ignore_dirs: list[str], extensions: list[str]) -> str:
    """Pass 1: path listing only. Minimum cost."""
    lines = []
    ignore_set = set(ignore_dirs)
    ext_set = set(extensions)
    for dirpath, dirnames, filenames in os.walk(root):
        # Avoid entering ignored directories
        dirnames[:] = [d for d in dirnames if d not in ignore_set]

        for fname in filenames:
            file = Path(dirpath) / fname
            if file.suffix in ext_set:
                lines.append(str(file.relative_to(root)))
    return "\n".join(sorted(lines))


def read_selected_files(root: Path, selected: list[str], max_lines: int = 40) -> str:
    """Pass 2: read only the files requested by the model."""
    snapshot = []
    for rel in selected:
        file = root / rel
        if not file.exists():
            continue
        try:
            lines = file.read_text(encoding="utf-8").splitlines()[:max_lines]
            snapshot.append(f"\n--- {rel} ---\n" + "\n".join(lines))
        except Exception:
            continue
    return "\n".join(snapshot)


def call_gemini(
    prompt: str,
    orchestratorel: str,
    logs_dir: Path | None = None,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    stage: str | None = None,
    span_id: str | None = None,
) -> tuple[str, dict, float]:
    """Wrapper with retry and logging."""
    client = get_gemini_client()
    for attempt in range(2):
        call_started = time.monotonic()
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
            )
            latency_ms = int((time.monotonic() - call_started) * 1000)
            raw = response.text.strip()

            # strip markdown fences
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            usage = response.usage_metadata
            tokens = {
                "input": usage.prompt_token_count,
                "output": usage.candidates_token_count,
            }
            cost = (
                tokens["input"] / 1_000_000 * COST_PER_1M_INPUT
                + tokens["output"] / 1_000_000 * COST_PER_1M_OUTPUT
            )

            log_call(
                agent=orchestratorel,
                prompt=prompt[:500],
                response=raw[:500],
                tokens=tokens,
                cost_usd=cost,
                logs_dir=logs_dir,
                trace_id=trace_id,
                run_id=run_id,
                stage=stage,
                span_id=span_id,
                model=MODEL,
                latency_ms=latency_ms,
            )

            return raw, tokens, cost

        except Exception as e:
            latency_ms = int((time.monotonic() - call_started) * 1000)
            if "429" in str(e) or "ResourceExhausted" in str(e):
                if attempt == 0:
                    print(f"[{orchestratorel}] Rate limit. Waiting 60s...")
                    time.sleep(60)
                    continue

            log_call(
                agent=orchestratorel,
                prompt=prompt[:500],
                response="",
                tokens={"input": 0, "output": 0},
                cost_usd=0.0,
                logs_dir=logs_dir,
                trace_id=trace_id,
                run_id=run_id,
                stage=stage,
                span_id=span_id,
                model=MODEL,
                latency_ms=latency_ms,
                error=str(e),
            )
            log_failure(
                trace_id=trace_id or "",
                run_id=run_id or "",
                stage=stage,
                error_type=FailureType.LLM_ERROR,
                message=f"Gemini call {orchestratorel} failed: {e}",
                source="agent",
                duration_ms=latency_ms,
                logs_dir=logs_dir,
            )
            raise

    raise ProviderError("gemini", f"[{orchestratorel}] Failed after retry.")


# ── prompts ────────────────────────────────────────────────────────────────

PASS1_PROMPT = """
You are a code reconnaissance agent. Your ONLY job is to select files for deeper analysis.

Given this file tree, select the 8-12 most architecturally important files:
- Entry points (main.py, app.py, index.ts)
- Core business logic
- Database models
- API route handlers
- Shared utilities with many dependents

File tree:
{file_tree}

Respond ONLY with a JSON array of relative paths. No explanation. No markdown.
Example: ["app/main.py", "app/models.py"]
"""

PASS2_PROMPT = """
You are a code reconnaissance agent. Observe, summarize, classify. Never implement.

Analyze these files and detect:
- Anti-patterns
- High complexity or risk areas
- Dependency hotspots
- Low-risk mechanical cleanup candidates

Files:
{file_contents}

Respond ONLY with valid JSON matching this exact schema. No explanation. No markdown:
{{
  "hotspots": [
    {{
      "file": "string",
      "issue": "string",
      "severity": "low|medium|high",
      "risk_level": "low|medium|high",
      "dependencies": ["string"]
    }}
  ],
  "recommended_order": ["string"],
  "risks": ["string"],
  "summary": "string"
}}
"""


# ── main ───────────────────────────────────────────────────────────────────


def run(
    config: Union[str, Path, TargetConfig],
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
) -> tuple[ScoutOutput, dict]:
    if isinstance(config, (str, Path)):
        config = TargetConfig.load(target_path=Path(config))

    root = config.target_path.resolve()
    logs_dir = config.workspace_path / "logs"
    print(f"[Scout] scanning {root} ...")

    # ── Pass 1: tree → file selection
    tree = read_file_tree(root, config.ignore_dirs, config.extensions)
    print(f"[Scout] {len(tree.splitlines())} files found. Asking Gemini to select...")

    raw1, tokens1, cost1 = call_gemini(
        PASS1_PROMPT.format(file_tree=tree),
        orchestratorel="scout_pass1",
        logs_dir=logs_dir,
        trace_id=trace_id,
        run_id=run_id,
        stage="scout",
        span_id="scout_pass1",
    )
    print(f"[Scout] Pass 1 done | tokens: {tokens1} | cost: ${cost1:.5f}")

    try:
        selected: list[str] = json.loads(raw1)
    except json.JSONDecodeError as e:
        print(f"[Scout] Pass 1 JSON parse error: {e}")
        print(f"[Scout] Raw output:\n{raw1}")
        log_failure(
            trace_id=trace_id or "",
            run_id=run_id or "",
            stage="scout",
            error_type=FailureType.SCHEMA_VALIDATION_ERROR,
            message=f"Scout pass1 parsing failed: {e}",
            source="agent",
            data={"span_id": "scout_pass1"},
            logs_dir=logs_dir,
        )
        raise
    print(f"[Scout] Selected {len(selected)} files: {selected}")

    # ── Pass 2: content → JSON diagnosis
    contents = read_selected_files(root, selected)
    print("[Scout] Reading selected files. Running analysis...")

    raw2, tokens2, cost2 = call_gemini(
        PASS2_PROMPT.format(file_contents=contents),
        orchestratorel="scout_pass2",
        logs_dir=logs_dir,
        trace_id=trace_id,
        run_id=run_id,
        stage="scout",
        span_id="scout_pass2",
    )

    total_cost = cost1 + cost2
    print(f"[Scout] Pass 2 done | tokens: {tokens2} | cost: ${cost2:.5f}")
    print(f"[Scout] Total cost: ${total_cost:.5f}")

    # ── Validate schema pass 2
    try:
        data = json.loads(raw2)
    except json.JSONDecodeError as e:
        print(f"[Scout] JSON parse error: {e}")
        print(f"[Scout] Raw output:\n{raw2}")
        log_failure(
            trace_id=trace_id or "",
            run_id=run_id or "",
            stage="scout",
            error_type=FailureType.SCHEMA_VALIDATION_ERROR,
            message=f"Scout pass2 parsing failed: {e}",
            source="agent",
            data={"span_id": "scout_pass2"},
            logs_dir=logs_dir,
        )
        raise

    try:
        output = ScoutOutput(**data)
    except Exception as e:
        print(f"[Scout] Schema validation error: {e}")
        log_failure(
            trace_id=trace_id or "",
            run_id=run_id or "",
            stage="scout",
            error_type=FailureType.SCHEMA_VALIDATION_ERROR,
            message=f"Scout schema validation failed: {e}",
            source="agent",
            logs_dir=logs_dir,
        )
        raise

    meta = {
        "tokens_input": tokens1["input"] + tokens2["input"],
        "tokens_output": tokens1["output"] + tokens2["output"],
        "cost_usd": total_cost,
        "model_used": MODEL,
    }

    return output, meta


# ── entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "targets/loja_app"
    result, _ = run(target)
    print("\n-- Scout Output --")
    print(json.dumps(result.model_dump(), indent=2))
