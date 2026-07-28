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
# Use the run_id printed by scan for the remaining stages.
patchforge plan <run_id>
patchforge preview <run_id>
patchforge apply <run_id>
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

- **Delivery:** V1 and P0–P4 are complete, including Validator Plugins
  (#282) and its V2 operational integration.
- **Priority:** Observe external users solving real problems before selecting
  the next product development priority. Scoped P5 items are backlog, not
  active work.
- **QA:** CI verifies the full test suite, Ruff lint, and Ruff formatting on every change. See the [CI workflow](https://github.com/Argenis1412/PatchForge/actions/workflows/ci.yml) for the current result.
- [Project context](./docs/context/CONTEXT.md) | [Development workflow](./docs/context/Workflow.md) | [Roadmap](./docs/planning/roadmap.md)

## Subprocess timeouts

`orchestrator.json` can set an immutable, per-run timeout policy:

```json
{
  "timeouts": {
    "validator_run": 450,
    "git_op": 30,
    "patch_apply": 30,
    "format_run": 60,
    "doctor_probe": 30
  }
}
```

For each field, precedence is defaults, configuration file, environment, then
CLI. Environment overrides use `PATCHFORGE_TIMEOUT_<FIELD>` (for example,
`PATCHFORGE_TIMEOUT_PATCH_APPLY=120`). Commands accept repeatable
`--timeout NAME=SECONDS` overrides, such as `patchforge ci ... --timeout
patch_apply=120`. `validator_timeout`, `PATCHFORGE_VALIDATOR_TIMEOUT`, and
`preview --validator-timeout` remain compatible aliases for `validator_run`.

## Quickstart

```bash
pip install -e ".[dev]"
patchforge scan ./your-project --workspace /tmp/patchforge-workspace
```

## Development

```bash
# Quick QA (portable on PowerShell, macOS, and Linux)
python -m ruff check .
python -m ruff format --check .
python -m pytest tests/ -v -n auto

# Auto-fix lint and formatting (opt-in)
python -m ruff check --fix .
python -m ruff format .
```

The repository also includes a `Makefile` with equivalent `make qa`, `make lint`,
`make format`, `make test`, and `make fix` shortcuts for environments where GNU
Make is installed. PowerShell and Windows do not provide `make` by default.

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

## Contributing & Security

- Want to contribute? See [CONTRIBUTING.md](./CONTRIBUTING.md) for the development workflow, branch naming, and QA gates.
- Found a security issue? See [SECURITY.md](./SECURITY.md) for how to report it.
