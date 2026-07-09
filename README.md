# PatchForge

[![CI](https://github.com/Argenis1412/PatchForge/actions/workflows/ci.yml/badge.svg)](https://github.com/Argenis1412/PatchForge/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

AI-powered, safety-first code modification tool. Generates, validates, and applies patches through a deterministic Plan → Preview → Validate → Apply pipeline.

> **AI proposes. PatchForge proves. Humans decide.**

## Workflow

```bash
patchforge doctor .
patchforge scan .
patchforge plan .
patchforge preview .
patchforge apply run_001
```

The internal runtime uses specialized agents, typed Pydantic contracts, and structured observability. The user-facing product is organized around repositories, plans, patches, validation, and Git review.

## Repository Safety Contract

PatchForge SHALL NOT modify repository contents unless:

1. A patch exists.
2. Validation succeeded.
3. Repository state is compatible.
4. User explicitly executes `apply`.

See [ADR-0003](./docs/adr/ADR-0003-product-contract.md) for the binding product contract and patch lifecycle.

## What Makes PatchForge Different?

Most AI coding tools optimize for speed. PatchForge optimizes for trust — changes are always reviewable before repository modification. See the [Product Thesis](./docs/product-thesis-v2.md) for a detailed competitive analysis.

## Current Status

- **Phase:** P3 — Async Workers & CI/CD Integration (P0/P1/P2 complete)
- **QA:** pytest 683 passed, 2 skipped | `ruff check .` → 0 errors | `ruff format --check` → clean
- [Phase 2 Roadmap](./docs/planning/roadmap-phase2.md) | [Full project context](./docs/context/CONTEXT.md)

## Quickstart

```bash
pip install -e ".[dev]"
patchforge scan ./your-project --workspace /tmp/patchforge-workspace
```

## Development

```bash
# Quick QA (lint + format check + tests)
make qa

# Individual steps:
make lint       # ruff check
make format     # ruff format --check
make test       # pytest -v
make fix        # auto-fix lint and format
```

## Docker

```bash
docker build -t patchforge:latest .
docker run --rm -v /path/to/repo:/repo -v /path/to/workspace:/workspace \
  patchforge:latest patchforge scan /repo --workspace /workspace
```

Requires at least one API key (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or `OPENROUTER_API_KEY`). See [docs](./docs/index.md) for full Docker usage, environment variables, and volume mounts.

## Design Goals

- **Git-native safety** — changes are reviewable with normal Git commands
- **Artifacts over magic** — findings, plans, patches, and validation reports are persisted
- **Contracts over prompts** — internal stages communicate through typed schemas
- **Small reliable changes** — bounded refactors beat broad unreliable automation
- **Human approval** — repository modification happens only at `apply`