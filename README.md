# PatchForge

[![CI](https://github.com/Argenis1412/PatchForge/actions/workflows/ci.yml/badge.svg)](https://github.com/Argenis1412/PatchForge/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)



PatchForge is a Git-native refactoring engine for real repositories: generate,
validate, and apply reviewable code patches safely.

## Philosophy

PatchForge is built around a simple principle:

> AI proposes. PatchForge proves. Humans decide.

See:
- [Product Thesis](./docs/PRODUCT_THESIS.md) — Why PatchForge exists, its principles, and competitive moat.
- [ADR-0003: Product Contract](./docs/adr/ADR-0003-product-contract.md) — The binding repository safety contract and patch lifecycle.

## Why PatchForge Exists

Most AI coding tools optimize for speed.

PatchForge optimizes for trust.

Instead of modifying repositories immediately, PatchForge separates:

```text
Scan → Plan → Patch → Validation → Apply
```

Every change remains reviewable before repository modification.

The long-term product workflow is intentionally simple:

```bash
patchforge doctor .
patchforge scan .
patchforge plan .
patchforge preview .
patchforge apply run_001
```

The internal runtime may use specialized agents, typed Pydantic contracts, checkpoints, model
routing, and structured observability. Those are implementation details. The user-facing product is
organized around repositories, plans, patches, validation, and Git review.

## Repository Safety Contract

PatchForge SHALL NOT modify repository contents unless:

1. A patch exists.
2. Validation succeeded.
3. Repository state is compatible.
4. User explicitly executes `apply`.

Current implementation caveat: today, the default workspace is created under the target repository
(`./workspace`). That means `patchforge scan ./your-project` may write orchestrator artifacts inside
the target working tree before `apply` is available. Until the workspace redesign lands, pass an external
workspace path when you want strict no-target-write behavior:

```bash
patchforge scan ./your-project --workspace /tmp/patchforge-workspace
```

The product contract means:

- `doctor` checks repository and environment readiness.
- `scan` analyzes the repository and writes findings as artifacts.
- `plan` proposes bounded tasks without generating or applying changes.
- `preview` generates a patch artifact and validation report without touching the working tree.
- `apply` is the only command allowed to modify the repository, and it must do so through Git safety checks.

See [ADR-0003: Product Contract — Reviewable Patch Workflow](./docs/adr/ADR-0003-product-contract.md)
for the binding product direction and patch lifecycle.

## What Makes PatchForge Different?

Most tools focus on autonomous code generation.

PatchForge focuses on repository safety.

| Tool | Primary Goal |
|------|-------------|
| Aider | Fast iteration |
| OpenHands | Autonomous execution |
| Plandex | Large-context planning |
| **PatchForge** | **Reviewable, auditable patches** |

See the [Product Thesis](./docs/PRODUCT_THESIS.md) for a detailed competitive analysis.

## Target Architecture

```mermaid
flowchart LR
    Repo[Repository] --> Scan[Scan]
    Scan --> Findings[Findings]
    Findings --> Plan[Plan]
    Plan --> Patch[Patch]
    Patch --> Validation[Validation]
    Validation --> Apply[Apply]
    Apply --> GitReview[Git Review]
```

A mature run should produce a self-contained artifact tree:

```text
workspace/
└── runs/
    └── run_001/
        ├── run.json
        ├── findings.json
        ├── plan.json
        ├── patch.diff
        ├── validation.json
        └── events.jsonl
```

The patch is the unit of value. A successful run is not “all agents completed”; it is a reviewable
patch, successful validation, and explicit human approval before repository modification.

## Current Implementation Status

The repository currently contains the runtime foundation:

- Typer CLI entrypoint with `scan` and `run` commands.
- Internal Scout, Architect, Executor, and Validator stages.
- Pydantic schemas for stage contracts.
- Workspace, logs, outputs, and pipeline run persistence.
- Provider clients and explicit environment bootstrap.
- Structured events and failure reporting.

The product roadmap is now focused on moving from an agent-stage pipeline toward an explicit
Scan → Plan → Preview → Apply workflow. See [Product Roadmap](./docs/ROADMAP.md).

## Immediate Roadmap

The next phases are intentionally narrow:

1. **Product contract and docs** — align terminology around reviewable patches.
2. **`doctor`** — verify Git, Python, Ruff, Pytest, workspace, and environment readiness.
3. **Separate `plan` from `preview`** — make intent and patch generation distinct.
4. **Run artifact redesign** — persist `workspace/runs/{run_id}/` as the product unit.
5. **Git-safe `apply`** — apply patches only through explicit Git checks and branch creation.
6. **Risk budgets** — add `--risk-budget`, `--max-files`, and `--max-diff-lines`.
7. **Patch lifecycle management** — validate patch state before apply: VALID, STALE, REBASEABLE, or CONFLICT.

V1 is scoped to Python repositories using Git, Ruff, and Pytest. TypeScript, monorepos, migration
packs, CI review, and autonomous bug investigation are deferred until the patch workflow is reliable.

## Quickstart

```bash
git clone https://github.com/Argenis1412/PatchForge.git
cd PatchForge
pip install -e .

# Current available analysis command. Use an external workspace to avoid
# writing orchestrator artifacts under ./your-project/workspace.
patchforge scan ./your-project --workspace /tmp/patchforge-workspace
```

## Why this direction?

Most agent frameworks expose internal orchestration as the product. PatchForge takes the
opposite approach: the runtime can be agentic internally, but the product should feel like a normal
engineering tool.

The design goals are:

- **Git-native safety** — changes are reviewable with normal Git commands.
- **Artifacts over magic** — findings, plans, patches, and validation reports are persisted.
- **Contracts over prompts** — internal stages communicate through typed schemas.
- **Small reliable changes** — bounded refactors beat broad unreliable automation.
- **Human approval** — repository modification happens only at `apply`.

## Non-goals for V1

PatchForge V1 is **NOT**:

- A general-purpose agent framework.
- A chatbot or conversational IDE.
- A migration engine for framework major versions.
- A CI review bot.
- A monorepo platform.
- A Terraform/infrastructure automation tool.
- A no-code automation tool.

These may be explored later only after the Git-native patch workflow is reliable.

## Repository Structure

```text
src/
└── orchestrator/
    ├── main.py          # CLI entry point
    ├── pipeline.py      # Pipeline execution engine
    ├── agents/          # Internal implementation stages
    ├── schemas/         # Typed contracts (Pydantic models)
    ├── clients/         # LLM provider clients
    └── observability/   # Structured logging & telemetry
tests/
docs/
```

## Development

```bash
pip install -e ".[dev]"
pytest -v
ruff check src/
```

For more details, see the [documentation](./docs/index.md).
