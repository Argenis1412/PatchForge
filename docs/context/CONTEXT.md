# PatchForge Project Context

> Last updated: 2026-08-01
> Read [AGENTS.md](../../AGENTS.md) before this document. This is the
> canonical current project state, not a changelog.

---

## Current Project State

- **Product:** PatchForge is an AI-assisted, safety-first code modification
  tool. Its deterministic product pipeline is Plan -> Preview -> Validate ->
  Apply; a human explicitly invokes `apply`.
- **Delivery state:** V1, P0, P1, P2, P3, P4, and Validator Plugins (#282)
  are complete. Validator V2 operational integration, timeout policy, doctor
  diagnostics, candidate promotion, and the unbound executable-plan rejection
  are delivered. The latter merged in [PR #300](https://github.com/Argenis1412/PatchForge/pull/300).
- **Main branch:** Stable. CI runs the full test suite, Ruff lint, and Ruff
  formatting on every change. Historical test counts are not live metrics.
- **Architecture:** Stable. No approved architectural migration is in
  progress.
- **Known blockers:** Full LLM dogfooding remains constrained by provider
  credits and is not a release gate.
- **Current priority:** Define the provider preflight and operator credential
  boundary, then observe one external user solving a real problem and document
  the problem, workflow, and evidence before selecting further product work.
- **P5 status:** Learning Pipeline items are scoped backlog, not active work.
  Do not start P5 merely because it is next in the old roadmap; choose the
  next development priority from external-use evidence.

## Product and Architecture Map

| Area | Responsibility |
| --- | --- |
| CLI | `doctor`, `scan`, `plan`, `preview`, `apply`, and `ci` expose the product workflow. |
| Pipeline | Sequences typed stage outputs and their persistence/reload boundaries. |
| Agents | Architect interprets findings; Executor prepares patches; Validator executes configured checks. |
| Persistence | Artifacts and run metadata are durable evidence and the source of truth between stages. |
| Validation | V1 compatibility and explicit V2 validator declarations produce fail-closed authorization decisions. |
| Workspace | Keeps generated artifacts and staging outside the target repository by default. |
| Git and apply | Applies only an approved, validated candidate through protected Git operations and recoverable promotion. |

The public model is repository -> scan -> plan -> patch -> validation -> apply.
Internal agent names are implementation details, not product terminology.

## Operational Boundaries

- `doctor` reports repository and validator readiness; it does not repair the
  target environment.
- `scan` is deterministic for Python and writes findings to the workspace.
  It does not modify the target repository.
- `plan` may use configured providers to turn findings or an issue file into
  bounded work. A plan is not a patch and is never authority to modify files.
- `preview` prepares a patch artifact and validation evidence in staging. It
  must leave the target working tree unchanged. A persisted
  `execution_plan.json` is rejected as an unauthorized transition input;
  absence of that artifact preserves the existing LLM executor path.
- `apply` is the only local command that changes target contents. It requires
  an existing patch, successful validation, compatible repository state, and
  explicit user invocation.
- `ci` composes the same guarded stages for automation. It must preserve the
  local product safety contract and keep external side effects outside the
  containerized pipeline boundary.

## Artifacts and Compatibility

- `run.json` is the run-level source of truth. Findings, plans, patches,
  validation reports, and apply results are persisted evidence for one run.
- Treat generated artifacts as untrusted input when crossing process or
  protocol boundaries. Validation evidence is not a reusable authorization
  credential unless the applicable protocol explicitly binds it.
- Versioned validator configuration is opt-in: versionless configuration
  remains the supported V1 compatibility profile; V2 declarations are
  explicit and fail closed.
- Candidate promotion uses an isolated candidate and policy captured from the
  unpatched base tree. Do not move validation after publication or reuse a
  receipt across protocols.
- Audit exports mirror run metadata structurally. Any field added to
  `RunMetadata` must be classified deliberately for redaction coverage.

## Architecture Invariants

Do not change these without an ADR. Read the linked record for rationale.

1. **Pipeline orchestrates only.** It may sequence stages, persist/reload
   artifacts, route typed schemas, and propagate typed failures. It must not
   own domain logic, Git operations, LLM calls, or patch generation. See
   [ADR-0002](../adr/ADR-0002-runtime-boundaries.md).
2. **Stage boundaries are typed.** Agents exchange Pydantic schemas, not raw
   dictionaries. Persisted inter-stage schemas must round-trip deterministically.
3. **Persistence is a transition boundary.** Every stage output is persisted,
   then reloaded before the next stage consumes it. In-memory handoff is
   forbidden.
4. **CLI is a surface, not a domain layer.** Commands parse inputs, call
   domain services, render output, and map errors; business logic belongs in
   dedicated modules.
5. **Product safety is binding.** No command before `apply` modifies the
   target working tree. `apply` requires a patch, successful validation,
   compatible repository state, and explicit user action. See
   [ADR-0003](../adr/ADR-0003-product-contract.md).
6. **Artifact compatibility is explicit.** Version persisted artifacts when
   their compatibility contract changes; additive defaults remain compatible
   only under [ADR-0004](../adr/ADR-0004-schema-versioning.md).
7. **Validator authorization fails closed.** V2 configuration and results use
   explicit policy and trusted adapters; historical unversioned artifacts are
   diagnostic evidence, never recovery credentials. See
   [ADR-0010](../adr/ADR-0010-validator-configuration-contract.md).
8. **Candidate promotion is bound to validated state.** Promotion validates an
   isolated candidate against base-tree policy and recovers only through its
   protocol-scoped receipt. See
   [ADR-0011](../adr/ADR-0011-apply-protocol-candidate-promotion.md).
9. **Executable-plan authority is explicit.** The presence of
   `execution_plan.json` cannot authorize Preview, Executor, or Validator
   transitions. Those boundaries fail closed with `unbound_execution_plan`
   until a separately designed compiler binds the artifact to the run's plan,
   base commit, tasks, paths, and budgets. See
   [ADR-0012](../adr/ADR-0012-unbound-execution-plan-authority.md).

## Active Debt and Decisions

- `apply.py` remains a complexity risk. Dogfooding-011 found the executor's
  large-file limit; do not refactor it before evidence from the next real-use
  evaluation. See [Dogfooding 011](../experiments/dogfooding-011.md).
- Technical discoveries and deferred work belong in
  [discoveries.md](discoveries.md), not here.
- The scoped P5 backlog and deferred initiatives live in the
  [issue registry](../planning/issue-registry.md) and
  [roadmap](../planning/roadmap.md); neither is an active implementation
  commitment.
- The audit-bundle manifest mirrors `RunMetadata` structurally so additive
  metadata remains represented without maintaining a duplicate field list.
  Redaction classifies every field explicitly; see the audit-bundle discovery
  and its regression test in [discoveries.md](discoveries.md).
- The deterministic execution-plan schema and executor remain internal
  mechanics. Dogfooding 013's manually injected artifact is historical
  evidence, not a supported authorization path. Any future compiler requires
  an independent issue and design with verifiable provenance.

## Priority Handoff

The current priority is intentionally singular. When it completes, replace it
with the next evidence-backed priority and update only the state, debt, or
invariant affected by that work. Do not turn completion notes into a timeline.

For the successor priority, capture the external user's problem, target
repository characteristics, attempted workflow, observed friction, and result
in an experiment or issue. Choose a new implementation only after reviewing
that evidence against the existing backlog.

## Documentation Ownership

| Document | Canonical responsibility |
| --- | --- |
| [AGENTS.md](../../AGENTS.md) | Session contract and cross-cutting agent rules. |
| [Workflow.md](Workflow.md) | Development process, QA, commits, pull requests, and AI roles. |
| This document | Current project state, architecture map, invariants, active debt, and one priority. |
| [ADRs](../adr/) | Architectural decisions and their rationale. |
| [Issue registry](../planning/issue-registry.md) | Issue inventory, status, and scoped backlog. |
| [Discoveries](discoveries.md) | Implementation discoveries and deferred technical debt. |

New completed work updates the current state and current priority above; it
does not append a historical implementation log to this file.
