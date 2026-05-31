# Product Roadmap — Git-native Reviewable Patches

## Product North Star

orchestrator-core is evolving toward a Git-native refactoring engine for real repositories.

The core promise is:

```text
Generate, validate, and apply reviewable code patches safely.
```

The target user workflow is:

```bash
orchestrator doctor .
orchestrator scan .
orchestrator plan .
orchestrator preview .
orchestrator apply run_001
```

The implementation may use agents, typed contracts, checkpoints, model routing, and observability,
but the user-facing workflow is organized around repository changes and patches.

## Product Principles

1. **Patch is the unit of value** — a run succeeds when it produces a useful, safe, reviewable diff.
2. **No magic writes** — no command before `apply` modifies the target repository working tree. Current default workspace behavior is a known gap until the run artifact redesign lands.
3. **Git-native review** — users should be able to inspect results with `git diff` and commit as usual.
4. **Small promises first** — a narrow, reliable workflow beats a broad, inconsistent platform.
5. **Artifacts over conversation** — every important output is persisted and auditable.
6. **Risk limits improve success** — bounded changes are more valuable than ambitious unreliable runs.

## Phase 0 — Product Contract and Documentation

Goal: align the repository around the reviewable patch workflow before expanding runtime features.

Deliverables:

- ADR-003 Product Contract accepted.
- README updated to describe the Git-native patch direction.
- Roadmap documented by phase.
- Internal agent names documented as implementation details, not product concepts.

Out of scope:

- runtime behavior changes
- new model providers
- migration support
- monorepo support

## Phase 1 — `doctor`

Goal: give users a safe first command that explains whether a repository is ready for orchestrator.

Target command:

```bash
orchestrator doctor .
```

Expected checks:

- Git repository detected.
- Current branch detected.
- Working tree cleanliness reported.
- Python project detected.
- Ruff availability detected or configured.
- Pytest availability detected or configured.
- Workspace path is writable.
- Required API keys or local model configuration are available when needed.

Success criteria:

- `doctor` never modifies the target repository.
- `doctor` reports when the configured workspace is inside the target repository.
- Output is understandable without knowing about internal agents.
- Failures include concrete next steps.

## Phase 2 — Separate `plan` from `preview`

Goal: separate intent from patch generation.

Target commands:

```bash
orchestrator scan .
orchestrator plan .
orchestrator preview .
```

Semantics:

- `scan` reads the repository and produces findings.
- `plan` turns findings into bounded tasks.
- `preview` generates a patch artifact and validation result without touching the working tree.

Success criteria:

- `plan` can be inspected before any patch is generated.
- `preview` produces a patch artifact that can be reviewed independently.
- `run --dry-run` ambiguity is removed or deprecated in favor of explicit commands.

## Phase 3 — Run Artifact Redesign

Goal: make each run self-contained, auditable, and safe to resume or apply.

Target layout:

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

Required metadata:

- run id
- target path
- base commit
- branch at scan/preview time
- goal
- status
- affected files
- patch checksum
- validation commands
- validation results
- risk level and reasons
- total cost and model metadata, when available

Success criteria:

- A run can be inspected without searching across stage-specific output files.
- `apply` can verify whether the patch is still valid for the target repository.
- Resume behavior is based on explicit run ids, not “latest file by timestamp.”

## Phase 4 — Git-safe `apply`

Goal: make repository modification an explicit, reviewable, Git-controlled step.

Target command:

```bash
orchestrator apply run_001
```

Required behavior:

1. Verify the target path is a Git repository.
2. Verify or report working tree cleanliness.
3. Verify the run exists and contains a patch.
4. Verify the patch checksum.
5. Verify the base commit or fail safely.
6. Check whether the patch applies.
7. Create an orchestrator branch, such as `orchestrator/run_001`.
8. Apply the patch.
9. Run configured validations.
10. Print normal Git review commands.

Success criteria:

- `apply` is the only command that modifies the target working tree.
- Failed apply attempts leave an understandable state.
- Users can review results with `git diff` before committing.

## Phase 5 — Risk Budgets and Change Limits

Goal: increase real-world success by limiting the size and risk of proposed changes.

Target options:

```bash
orchestrator plan . --risk-budget low
orchestrator preview . --max-files 5
orchestrator preview . --max-diff-lines 300
orchestrator plan . --exclude auth,payments
```

Success criteria:

- High-risk areas are not modified under a low risk budget.
- Runs fail safely when estimated changes exceed configured limits.
- Risk output includes reasons, not just a numeric score.

## Phase 6 — Python Framework Awareness

Goal: improve Python refactoring quality after the core patch workflow is reliable.

Candidate targets:

- FastAPI
- Django
- Flask
- SQLAlchemy
- Celery

Success criteria:

- Framework detection is evidence-based.
- Refactors preserve public routes, handlers, and common framework conventions.
- Framework support remains behind the same Scan → Plan → Preview → Apply workflow.

## Phase 7 — Python Monorepos

Goal: support repositories where the repo root is not the same as the refactoring target.

Target model:

```text
Workspace
 ├─ apps/api
 ├─ packages/shared
 └─ tools/scripts
```

Success criteria:

- Projects/modules are detected independently.
- Each module can have its own lint, test, and typecheck commands.
- Runs are scoped to a module unless explicitly expanded.

## Phase 8 — TypeScript

Goal: add TypeScript only after Python patch generation and apply safety are mature.

Candidate tooling:

- npm, pnpm, or yarn detection
- TypeScript compiler
- ESLint
- Vitest/Jest

Success criteria:

- TypeScript follows the same public workflow.
- Tooling detection is module-aware.
- Patch size and risk limits remain enforced.

## Phase 9 — Migration Packs

Goal: support larger version migrations through explicit, versioned migration packs rather than ad hoc model output.

Candidate commands:

```bash
orchestrator migrate detect .
orchestrator migrate plan fastapi@1
orchestrator migrate preview run_001
orchestrator migrate apply run_001
```

Migration packs should include:

- detectors
- rules
- codemods
- validators
- examples
- references to breaking changes

Success criteria:

- Migrations are rule-backed and testable.
- LLMs assist migration execution but do not invent the migration process from scratch.
- Migration patches remain reviewable and Git-safe.

## Phase 10 — CI Review

Goal: bring the patch workflow into pull requests without creating noisy bots.

Candidate command:

```bash
orchestrator review --changed-files-only
```

Success criteria:

- CI review is advisory by default.
- Comments include evidence and concrete suggestions.
- Suggested patches are artifacts, not automatic PR mutations.
- Teams can configure fail conditions for high-risk changes only.

## Deferred Ideas

These are not rejected, but they should not distract from the core product contract:

- general-purpose agent framework APIs
- user-authored DAGs
- autonomous debugging workflows
- infrastructure/Terraform changes
- broad multi-language support
- automated deployment or release orchestration
