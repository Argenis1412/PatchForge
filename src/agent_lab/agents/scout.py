# agents/scout.py
import os
import json
import time
import sys
from pathlib import Path
from typing import Union
from agent_lab.schemas.scout_output import ScoutOutput
from agent_lab.schemas.config import TargetConfig
from agent_lab.observability.logger import log_call
from agent_lab.clients.gemini_client import get_gemini_client

MODEL = "gemini-2.5-flash"

COST_PER_1M_INPUT  = 0.075
COST_PER_1M_OUTPUT = 0.30

# ── helpers ────────────────────────────────────────────────────────────────

def read_file_tree(root: Path, ignore_dirs: list[str], extensions: list[str]) -> str:
    """Pasada 1: solo lista de rutas. Costo mínimo."""
    lines = []
    ignore_set = set(ignore_dirs)
    ext_set = set(extensions)
    for dirpath, dirnames, filenames in os.walk(root):
        # Evitar entrar a carpetas ignoradas
        dirnames[:] = [d for d in dirnames if d not in ignore_set]
        
        for fname in filenames:
            file = Path(dirpath) / fname
            if file.suffix in ext_set:
                lines.append(str(file.relative_to(root)))
    return "\n".join(sorted(lines))


def read_selected_files(root: Path, selected: list[str], max_lines: int = 40) -> str:
    """Pasada 2: lee solo los archivos pedidos por el modelo."""
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


def call_gemini(prompt: str, agent_label: str, logs_dir: Path | None = None) -> tuple[str, dict, float]:
    """Wrapper con retry y logging."""
    client = get_gemini_client()
    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
            )
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
                "input":  usage.prompt_token_count,
                "output": usage.candidates_token_count,
            }
            cost = (
                tokens["input"]  / 1_000_000 * COST_PER_1M_INPUT +
                tokens["output"] / 1_000_000 * COST_PER_1M_OUTPUT
            )

            log_call(
                agent=agent_label,
                prompt=prompt[:500],   # no loguear prompt entero
                response=raw[:500],
                tokens=tokens,
                cost_usd=cost,
                logs_dir=logs_dir,
            )

            return raw, tokens, cost

        except Exception as e:
            if "429" in str(e) or "ResourceExhausted" in str(e):
                if attempt == 0:
                    print(f"[{agent_label}] Rate limit. Waiting 60s...")
                    time.sleep(60)
                    continue
            raise

    raise RuntimeError(f"[{agent_label}] Failed after retry.")


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

def run(config: Union[str, Path, TargetConfig]) -> tuple[ScoutOutput, dict]:
    if isinstance(config, (str, Path)):
        config = TargetConfig.load(target_path=Path(config))

    root = config.target_path.resolve()
    logs_dir = config.workspace_path / "logs"
    print(f"[Scout] scanning {root} ...")

    # ── Pasada 1: árbol → selección de archivos
    tree = read_file_tree(root, config.ignore_dirs, config.extensions)
    print(f"[Scout] {len(tree.splitlines())} files found. Asking Gemini to select...")

    raw1, tokens1, cost1 = call_gemini(
        PASS1_PROMPT.format(file_tree=tree),
        agent_label="scout_pass1",
        logs_dir=logs_dir,
    )
    print(f"[Scout] Pass 1 done | tokens: {tokens1} | cost: ${cost1:.5f}")

    selected: list[str] = json.loads(raw1)
    print(f"[Scout] Selected {len(selected)} files: {selected}")

    # ── Pasada 2: contenido → diagnóstico JSON
    contents = read_selected_files(root, selected)
    print("[Scout] Reading selected files. Running analysis...")

    raw2, tokens2, cost2 = call_gemini(
        PASS2_PROMPT.format(file_contents=contents),
        agent_label="scout_pass2",
        logs_dir=logs_dir,
    )

    total_cost = cost1 + cost2
    print(f"[Scout] Pass 2 done | tokens: {tokens2} | cost: ${cost2:.5f}")
    print(f"[Scout] Total cost: ${total_cost:.5f}")

    # ── Validar schema
    try:
        data = json.loads(raw2)
    except json.JSONDecodeError as e:
        print(f"[Scout] JSON parse error: {e}")
        print(f"[Scout] Raw output:\n{raw2}")
        raise

    output = ScoutOutput(**data)
    
    meta = {
        "tokens_input": tokens1["input"] + tokens2["input"],
        "tokens_output": tokens1["output"] + tokens2["output"],
        "cost_usd": total_cost,
        "model_used": MODEL
    }
    
    return output, meta


# ── entrypoint ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "targets/loja_app"
    result, _ = run(target)
    print("\n-- Scout Output --")
    print(json.dumps(result.model_dump(), indent=2))
