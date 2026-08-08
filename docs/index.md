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
- [Product Thesis](./product-thesis-v2.md) - Product definition, artifact contract, and non-goals (post-adversarial)
- [Roadmap](./planning/roadmap.md) - Scoped PatchForge Core backlog and deferred initiatives
- [Scout Vision](./planning/scout-vision.md) - Long-term vision for Scout as a separate future product line (frozen) — not the internal Scout pipeline stage below
- [Issue Registry](./planning/issue-registry.md) - Tracked issues with ACs, priorities, and dependencies
- [External Pilot 001](./experiments/external-pilot-001.md) - Sanitized protocol for the next external-use evaluation
- [ADR-0003: Product Contract](./adr/ADR-0003-product-contract.md) - Binding repository safety contract and patch lifecycle
- [Quality Gate](./QUALITY_GATE.md) - Pre-merge checklist

## Product Workflow

The target command flow is:

```bash
patchforge doctor <python-project-directory> --env-file <operator-env-file>
patchforge scan <python-project-directory> --workspace <workspace>
# Use the run_id printed by scan for the remaining stages.
patchforge plan <run_id> --workspace <workspace> --issue-file <issue.md> --env-file <operator-env-file>
patchforge preview <run_id> --workspace <workspace> --env-file <operator-env-file>
patchforge apply <run_id> --workspace <workspace> --env-file <operator-env-file>
```

Use credentials inherited by the shell, or pass an operator-owned
`--env-file` outside the target repository. An explicit file replaces inherited
provider credentials for that command invocation. `doctor` reports static
credential eligibility; it does not verify account balance, network access, or
runtime provider availability.

In a monorepo, pass the Python project directory explicitly. For example, if
the repository is `/path/to/repo` and the Python project is under `backend/`:

```bash
patchforge doctor /path/to/repo/backend --env-file /path/to/operator.env
patchforge scan /path/to/repo/backend --workspace /tmp/patchforge-workspace
```

PatchForge keeps the enclosing Git repository context while using the selected
Python directory for project checks and scanning.

The deterministic `scan` command requires a human-written Markdown issue file
for the next stage: pass it to `plan --issue-file`. Before `apply`, all stages
leave the target working tree unchanged.

The target safety rule is:

> No command before `apply` may modify the target repository working tree.

The default workspace is stored outside the target repository. Explicit `--workspace` paths are also
validated and rejected when they resolve inside the target repository.

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

- [ADR-0015: Attested Independent Review Evidence](./adr/ADR-0015-attested-independent-review-evidence.md)
- [ADR-0014: CI Preflight Rejection Result Contract](./adr/ADR-0014-ci-preflight-rejection-result-contract.md)
- [ADR-0013: Provider Preflight and Operator Credential Boundary](./adr/ADR-0013-provider-preflight-credential-boundary.md)
- [ADR-0005: IssueContract — Canonical Source-Agnostic Issue Schema](./adr/ADR-0005-issue-contract.md)
- [ADR-0004: Schema Versioning Policy](./adr/ADR-0004-schema-versioning.md)
- [ADR-0003: Product Contract — Reviewable Patch Workflow](./adr/ADR-0003-product-contract.md)
- [ADR-0002: Runtime Boundaries & Operational Hygiene](./adr/ADR-0002-runtime-boundaries.md)
- [ADR-0001: Architect Model Comparison](./adr/ADR-0001-architect-comparison.md)

## Current Status

- V1 and P0–P4 complete, including Validator Plugins (#282).
- ADR-0013 and its credential-boundary foundations are complete: explicit
  credential resolution, shared provider policy, and invocation-scoped runtime
  migration (issues #302, #304, #306, and #308).
- First-run onboarding (#310), effect-free provider preflight for local Plan
  and Preview stages (#312), and ADR-0014's initial Architect CI preflight
  result (#318 / PR #319) are complete.
- Current priority: observe one external user solving a real problem and
  document the evidence before selecting further product work.
- P5 Learning Pipeline items remain scoped backlog, not active work.
- QA: CI verifies the full test suite, Ruff lint, and Ruff formatting on every change. See the [CI workflow](https://github.com/Argenis1412/PatchForge/actions/workflows/ci.yml) for the current result.

See the [Roadmap](./planning/roadmap.md) for current priorities and
[Scout Vision](./planning/scout-vision.md) for the long-term second product line.

## Getting Started

### Installation

```bash
git clone https://github.com/Argenis1412/PatchForge.git
cd PatchForge
pip install -e .
```

### First Run

```bash
# Store credentials outside /path/to/repo, then run against its Python project:
patchforge doctor /path/to/repo/backend --env-file /path/to/operator.env
patchforge scan /path/to/repo/backend --workspace /tmp/patchforge-workspace
# Continue with plan --issue-file, preview, and apply as shown above.
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
