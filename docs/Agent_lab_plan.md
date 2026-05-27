# Agent Lab — Implementation Plan
> Current status: Scout ✅ | Architect (Gemini) ✅ | Architect (Claude) ✅ | Executor ⏳ | Validator ⏳

---

## 1. Context

Learning lab to build a multi-agent pipeline for code analysis and refactoring.
Target project: **Loja App** — payments system built with FastAPI + React.

**Lab Objective:**
- Learn AI agent orchestration without frameworks.
- Validate the Scout → Architect → Executor → Validator pattern.
- Control costs using cheap models for exploration tasks and premium models only for reasoning.

---

## 2. Pipeline Architecture

```
┌─────────────┐     JSON      ┌─────────────┐     Plan JSON   ┌─────────────┐
│    SCOUT    │ ────────────► │  ARCHITECT  │ ──────────────► │  EXECUTOR   │
│ Gemini Flash│               │ Claude Sonnet│                 │Gemini/Groq/C│
└─────────────┘               └─────────────┘                 └──────┬──────┘
  Read-only                    Validates, prioritizes                 │
  Observes, classifies         Detects edge cases              ┌──────▼──────┐
  Output: JSON schema          DOES NOT implement              │  VALIDATOR  │
                                                               │real scripts │
                                                               │+ LLM summary│
                                                               └──────┬──────┘
                                                                      │
                                                               ┌──────▼──────┐
                                                               │   REVIEWER  │
                                                               │Claude Sonnet│
                                                               │ONLY if diff │
                                                               │is sensitive │
                                                               └─────────────┘
```

### Layer Responsibilities

| Agent | Model | Can write files | Responsibility |
|---|---|---|---|
| Scout | Gemini Flash | ❌ NEVER | Scan, classify, generate JSON |
| Architect | Claude Sonnet | ❌ NEVER | Validate findings, prioritize, design order |
| Executor | Gemini / Groq / Claude | ✅ approved tasks only | Changes (LOW/MED), diff (HIGH) |
| Validator | Real scripts + LLM summary | ❌ | lint, tests, typecheck, stderr summary |
| Reviewer | Claude Sonnet | ❌ | High-risk diffs only |

**Contract**: Each agent `run()` method now returns a tuple `(Output, meta_dict)`, where `meta_dict` contains:
- `tokens_input` (int)
- `tokens_output` (int)
- `cost_usd` (float)
- `model_used` (str)

---

## 3. Golden Rule: Scout NEVER implements

```python
class ScoutAgent:
    ALLOWED_TOOLS   = ["read_file", "list_dir", "grep"]
    FORBIDDEN_TOOLS = ["write_file", "run_command", "delete_file"]
```

If the Scout has write permissions, the pipeline loses its control point.
This is not about trust in the model — it is a design constraint.

---

## 4. Risk Policy (Executor Routing)

```
LOW RISK → Gemini Flash (Executor)
  - variable renaming
  - adding type hints
  - moving imports
  - code formatting
  - removing console.log / debug print
  - linting fixes (noqa, line length)

MEDIUM RISK → Groq (Llama 3 70B)
  - function refactoring with side effects
  - internal interface change
  - alteration of non-critical middleware

HIGH RISK → Claude + mandatory human review
  - anything touching migrations
  - auth / payments changes
  - API contract alterations
  - financial logic (recibo.py, calculadora.py)
```

---

## 5. Project Structure

```
agent-lab/
├── .env                        # API keys — never commit
├── pipeline.py                 # manual orchestration (pending)
├── logger.py                   # logging of all calls
├── schemas/
│   ├── __init__.py
│   ├── scout_output.py         # ✅ implemented
│   └── architect_output.py     # ⏳ pending
├── agents/
│   ├── __init__.py
│   ├── scout.py                # ✅ Gemini Flash — two passes
│   ├── architect.py            # ⚠️  ran with Gemini, pending Claude
│   ├── executor.py             # ⏳ pending
│   └── validator.py            # ⏳ pending
├── logs/
│   ├── scout_pass1.log
│   ├── scout_pass2.log
│   └── architect.log
└── targets/
    └── loja_app/               # target repo
```

---

## 6. Implemented Schemas

### ScoutOutput (`schemas/scout_output.py`) ✅

```python
from pydantic import BaseModel
from typing import Literal

class Hotspot(BaseModel):
    file: str
    issue: str
    severity: Literal["low", "medium", "high"]
    risk_level: Literal["low", "medium", "high"]
    dependencies: list[str]

class ScoutOutput(BaseModel):
    hotspots: list[Hotspot]
    recommended_order: list[str]
    risks: list[str]
    summary: str
```

### ArchitectOutput (`schemas/architect_output.py`) ⏳ pending

```python
from pydantic import BaseModel
from typing import Literal

class Task(BaseModel):
    task_id: str
    title: str
    description: str
    files_to_modify: list[str]
    priority: Literal["low", "medium", "high"]
    effort: Literal["low", "medium", "high"]
    risk_level: Literal["low", "medium", "high"]
    dependencies: list[str]

class ArchitectOutput(BaseModel):
    validated_findings: list[str]
    false_positives: list[str]
    systemic_risks: list[str]
    implementation_plan: list[Task]
    blockers: list[str]
```

---

## 7. Current Implementation Status

### ✅ Scout — Complete

- Two-pass strategy (tree → selection → analysis)
- Pass 1: file tree only, < 5K tokens, cost ~$0.001
- Pass 2: reads 8-12 selected files, cost ~$0.003
- Automatic retry on 429 (rate limit)
- Token and cost logging per pass
- Windows infinite loop protection (os.walk)
- SDK: `google-genai` (new, not deprecated)

**Real cost of Scout on Loja App: ~$0.004**

### ⚠️ Architect — Complete

**Results obtained (logs in `agent-lab/logs/`):**
- Gemini Flash executed successfully ($0.00040).
- Claude Sonnet (`claude-sonnet-4-6`) executed successfully ($0.03562).
- Both correctly validated against the schema.

**Comparison:**
- Claude demonstrated greater depth by identifying *false positives* and systemic risks (e.g., lack of tests, environment disparity) that Gemini missed.
- The cost difference is notable (Ratio ~88x), justifying the strategic use of premium models only for Architect/Reviewer tasks.

**Lessons learned:**
| Problem encountered | Real cause | Applied solution |
|---|---|---|
| 404 Error in Claude Sonnet | Outdated model name | Use `claude-sonnet-4-6` |

### ⏳ Executor — Pending

- Implement routing by risk_level
- DeepSeek for LOW tasks
- Claude for MEDIUM/HIGH tasks
- Never execute without Architect's validated output

### ✅ Validator — Complete
- ruff check on full project
- pytest with rc=5 treated as PASS (no tests yet)
- tsc --noEmit with dynamic detection of frontend/
- Gemini Flash summarizes stderr only if there are failures
- load_dotenv with explicit path to agent-lab .env

---

## 8. Lessons Learned So Far

| Problem encountered | Real cause | Applied solution |
|---|---|---|
| Infinite loop on Windows | `rglob` traverses junctions before filtering | Switch to `os.walk` with IGNORE_DIRS |
| 429 with 50 lines per file | Payload > 250K tokens free tier | Two-pass strategy (tree → selection) |
| Deprecated SDK `google.generativeai` | Google migrated to `google-genai` | `uv remove google-generativeai && uv add google-genai` |
| 404 Error in Claude Sonnet | Outdated model name | Use `claude-sonnet-4-6` — claude-3-x models no longer available |
| JSON with markdown fences | Gemini sometimes adds ```json | Explicit strip before `json.loads` |
| ruff writes to stdout, not stderr | Different convention than pytest/tsc | _summarize_errors uses r.stderr or r.stdout |
| pytest rc=5 is not failure | "no tests collected" is empty state | passed = rc in (0, 5) |
| llama3-70b-8192 deprecated in Groq | Groq retired model without warning | Migrate to llama-3.3-70b-versatile |

---

## 9. Real vs Estimated Costs

| Agent | Model | Estimated cost/call | Observed real cost |
|---|---|---|---|
| Scout (2 passes) | Gemini Flash | ~$0.004 | **$0.004** ✅ |
| Architect | Claude Sonnet | ~$0.013 | ~$0.035 ✅ |
| Executor (LOW/MED) | Gemini / Groq | $0.00 (Free) | pending |
| Validator | Scripts + LLM | ~$0.002 | pending |

**Available Claude Budget: ~$2.50**
**Possible Architect calls: ~190**

---

## 10. Next Steps in Order

```
[✅] 1. Create schemas/architect_output.py with Pydantic
[✅] 2. Rewrite architect.py to use Claude Sonnet
[✅] 3. Run Claude on scout_output_sample.json
[✅] 4. Compare Claude output vs Gemini Architect output
[✅] 5. Document differences — this validates the correct model per layer
[✅] 6. Implement executor.py with routing by risk_level (branch: `feat/phase-6-executor`)
[✅] 7. Implement validator.py with real scripts (branch: `feat/phase-7-validator`)
[✅] 8. Connect pipeline.py end-to-end (branch: `feat/phase-8-pipeline`)
[✅] 9. Run full pipeline on loja_app (branch: `feat/phase-8-pipeline`)
[✅] 9. Run full pipeline on loja_app (branch: `feat/phase-8-pipeline`)
[✅] 10. Quality & Testing (Phase 2): Standardize tests & Quality Gate
[ ] 11. Finalize ADR: Document the finalized contract and operational policies. (branch: `feat/phase-9-final-docs`)
```

---

## 11. Lab Rules

1. **Scout is read-only.** No exceptions.
2. **Logging from day 1.** Prompt, response, tokens, latency, cost.
3. **Schema first.** Define the contract between agents before writing the agent.
4. **Validator uses real scripts.** LLM only summarizes stderr.
5. **Claude acts as reviewer, not explorer.**
6. **Any model change must be documented.** Do not switch Gemini↔Claude without logging it.
7. **Explicit retry policy per agent.** Invalid JSON → 1 retry. Hallucinated path → reject.
8. **PR workflow:** Each PR is worked on sequentially only: branch → changes → ruff + tests → commit → push → PR → merge → git checkout main && git pull → next PR.

---

## 12. Configured APIs

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...    # Claude Sonnet — Architect + Reviewer + Executor (HIGH)
GOOGLE_API_KEY=...               # Gemini Flash — Scout + Executor (LOW)
GROQ_API_KEY=...                 # Groq Llama 3 — Executor (MEDIUM)
```

**Installed SDKs:**
```toml
anthropic = "*"
google-genai = "*"
pydantic = "*"
python-dotenv = "*"
httpx = "*"
```
