# PatchForge Documentation

PatchForge is a Git-native refactoring engine for real repositories. Its goal
is to generate, validate, and apply reviewable code patches safely.

The internal runtime can use agents, typed contracts, provider routing, checkpoints, and structured
observability. The public product model is simpler:

```text
Repository → Scan → Plan → Patch → Validation → Apply
```

## Quick Links

- [README](../README.md) - Project overview and quickstart
- [Product Thesis V2](./product-thesis-v2.md) - Product definition, artifact contract, and non-goals (post-adversarial)
- [Product Thesis](./PRODUCT_THESIS.md) - Why PatchForge exists, its principles, and competitive moat
- [Product Roadmap](./ROADMAP.md) - Phased plan toward the reviewable patch workflow
- [Issue Registry](./planning/issue-registry.md) - Tracked issues with ACs, priorities, and dependencies (ADR-01 decomposed)
- [ADR-0003: Product Contract](./adr/ADR-0003-product-contract.md) - Binding repository safety contract and patch lifecycle
- [Quality Gate](./QUALITY_GATE.md) - Pre-merge checklist

## Product Workflow

The target command flow is:

```bash
patchforge doctor .
patchforge scan .
patchforge plan .
patchforge preview .
patchforge apply run_001
```

The target safety rule is:

> No command before `apply` may modify the target repository working tree.

Current caveat: the implementation currently defaults workspace writes to `<target>/workspace`. Use
`--workspace /tmp/patchforge-workspace` for scans when you want to keep generated artifacts outside
the target repository until the workspace redesign is implemented.

### Product Concepts

1. **Doctor** - Checks whether the repository and environment are ready.
2. **Scan** - Reads the repository and produces findings.
3. **Plan** - Converts findings into bounded, reviewable tasks.
4. **Preview** - Generates a patch artifact and validation report without touching the working tree.
5. **Apply** - Applies an existing patch through Git safety checks.

### Internal Runtime Concepts

The current implementation still contains internal stages:

1. **Scout** - Repository analysis and findings generation.
2. **Architect** - Findings validation and implementation planning.
3. **Executor** - Current implementation stage that will evolve toward patch generation.
4. **Validator** - Tool execution and validation summaries.

These are implementation details. Public documentation and UX should prefer the product concepts:
Scan, Plan, Patch, Validation, Apply, and Run.

## Decision Records

Design and architecture decisions are documented in Architecture Decision Records (ADRs):

- [ADR-0004: Schema Versioning Policy](./adr/ADR-0004-schema-versioning.md)
- [ADR-0003: Product Contract — Reviewable Patch Workflow](./adr/ADR-0003-product-contract.md)
- [ADR-0002: Runtime Boundaries & Operational Hygiene](./adr/ADR-0002-runtime-boundaries.md)
- [ADR-0001: Architect Model Comparison](./adr/ADR-0001-architect-comparison.md)

## Roadmap Summary

The current plan is intentionally narrow:

1. Product contract and documentation alignment.
2. `doctor` command.
3. Separate `plan` from `preview`.
4. Self-contained `workspace/runs/{run_id}/` artifacts.
5. Git-safe `apply`.
6. Risk budgets and change limits.
7. Python framework awareness.
8. Python monorepos.
9. TypeScript.
10. Migration packs.
11. CI review.

See [Product Roadmap](./ROADMAP.md) for details.

## Getting Started

### Installation

```bash
git clone https://github.com/Argenis1412/PatchForge.git
cd PatchForge
pip install -e .
```

### Configuration

Create a `.env` file with your API keys:

```bash
cp .env.example .env
# Edit .env and add your API keys
```

### First Run

```bash
patchforge scan /path/to/project --workspace /tmp/patchforge-workspace
```

## Development

### Setup Development Environment

```bash
# Clone and navigate
git clone https://github.com/Argenis1412/PatchForge.git
cd PatchForge

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest -v
```

### Running Tests

```bash
# Run all tests
pytest -v

# Run with coverage
pytest --cov=src/orchestrator tests/
```

### Code Quality

```bash
# Lint code
ruff check src/

# Format code
ruff format src/
```

## Support

- Open an issue for bug reports.
- Start a discussion for feature requests.
- Check the README, roadmap, and ADRs first.

## License

This project is licensed under the MIT License - see [LICENSE](../LICENSE) for details.
