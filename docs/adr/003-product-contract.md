# ADR-003: Product Contract — Reviewable Patch Workflow

## Status

Accepted as product direction (May 2026)

## Context

orchestrator-core started as a multi-stage agent runtime with internal roles such as Scout,
Architect, Executor, and Validator. That architecture is still useful internally, but it is not
the product a developer should have to understand.

The user-facing value is not an agent run. The user-facing value is a safe, reviewable patch for
a real repository.

The product model is therefore shifting from:

```text
Multi-agent runtime
 ├─ Scout
 ├─ Architect
 ├─ Executor
 └─ Validator
```

to:

```text
Repository
 ↓
 Scan
 ↓
 Plan
 ↓
 Patch
 ↓
 Validation
 ↓
 Apply
```

This ADR defines the public contract that keeps that product simple while the internal runtime can
continue to use typed contracts, model routing, observability, checkpoints, and specialized agents.

## Decision

orchestrator-core will be positioned and evolved as a Git-native refactoring engine that generates,
validates, and applies reviewable patches.

The public workflow is:

```bash
orchestrator doctor .
orchestrator scan .
orchestrator plan .
orchestrator preview .
orchestrator apply run_001
```

Internal agent names are implementation details. They may remain in code, traces, and developer
handoff notes, but they should not be required concepts in the primary user experience.

## Public Concepts

The product-level concepts are:

- **Repository** — the Git repository or module being analyzed.
- **Run** — a persisted unit of work for one proposed change sequence.
- **Scan** — read-only repository analysis that produces findings.
- **Findings** — observed hotspots, risks, detected tooling, and repository facts.
- **Plan** — ordered, bounded tasks that explain what should change and why.
- **Patch** — a unified diff generated as an artifact for human review.
- **Validation** — test, lint, type, or custom checks executed against the proposed patch.
- **Apply** — the only operation allowed to modify the target repository working tree.

Implementation-level concepts include:

- Scout
- Architect
- Executor
- Validator
- model routing
- retries
- prompt internals
- provider selection

These may appear in internal documentation and logs, but the product UX should prefer the public
concepts above.

## Safety Rule

Before `apply`, there must be **zero target repository modifications**.

This is the target product contract. The current implementation still defaults the workspace to
`<target>/workspace`, so current commands can write artifacts inside the target repository unless an
external `--workspace` path is provided. Closing that gap is part of the run artifact redesign.

Allowed before `apply`:

- read files from the target repository
- inspect Git metadata
- run analysis commands
- write artifacts under the orchestrator workspace
- generate a patch file as an artifact
- run validation in a sandbox or other non-mutating environment

Forbidden before `apply`:

- editing target repository files
- staging files
- creating commits
- changing branches
- applying patches to the user's working tree
- modifying dependency lockfiles in place

This rule is the core trust boundary of the product.

## Git Rule

Every `apply` operation must be Git-safe.

At minimum, `apply` must:

1. Verify that the target path is a Git repository.
2. Record and verify the base commit used to generate the patch.
3. Check whether the working tree is clean, or require an explicit override.
4. Validate that the patch still applies.
5. Create or use an explicit orchestrator branch.
6. Apply the patch.
7. Run configured validations.
8. Report the final review commands, such as `git diff`, `git status`, and `git commit`.

## Run Artifact Layout

Runs should become self-contained product artifacts. The target layout is:

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

The run manifest should record enough information to make the run auditable and safe to apply:

- run id
- target path
- base commit
- current branch at scan/preview time
- status
- goal
- artifact paths
- affected files
- patch checksum
- validation commands and results
- risk level and risk reasons
- model/provider metadata, when available

## Success Definitions

A successful `preview` means:

- findings were generated or reused
- a plan was generated or reused
- a patch was generated as an artifact
- validations were executed or explicitly skipped with a recorded reason
- the target repository working tree was not modified

A successful `apply` means:

- the user selected a run explicitly
- the target Git repository was verified
- the patch matched the expected base state or failed safely
- the patch was applied on an orchestrator-controlled branch
- validations were executed after applying
- the user can review the result with normal Git commands

A successful product experience is not “all agents completed.” It is:

```text
reviewable patch
+
successful validation
+
explicit human approval before repository modification
```

## V1 Scope

The first product target is intentionally narrow:

- Python repositories
- Git repositories
- Ruff for linting
- Pytest for tests
- small and medium refactors
- reviewable patches
- conservative apply semantics

## Explicit Non-goals for V1

The following are deferred until the reviewable patch workflow is reliable:

- TypeScript support
- monorepo project graphs
- framework migration packs
- CI review bots
- autonomous bug investigation
- Terraform or infrastructure changes
- general-purpose agent framework APIs
- complex DAG authoring by end users

## Consequences

- The unit of value becomes the patch, not the agent.
- Internal agents remain useful, but they are hidden behind product concepts.
- The Executor role must evolve from “apply changes” toward “generate patch artifacts.”
- Validation must be able to reason about a proposed patch without violating the safety rule.
- `apply` becomes a distinct Git safety boundary, not a side effect of `run`.
- Product documentation should lead with Scan, Plan, Preview, Apply instead of Scout,
  Architect, Executor, Validator.
