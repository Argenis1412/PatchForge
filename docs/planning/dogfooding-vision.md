# Vision: PatchForge Dogfooding

## The Concept

PatchForge transitions from a tool operated by a human to a system that can execute predefined, well-scoped issues against its own source code.

Instead of autonomous "self-improvement" (which is risky and ambiguous), we implement **disciplined dogfooding**. We define the "better" state via concrete Issues with Acceptance Criteria (AC), and the system implements them.

## Strategic Positioning

Dogfooding is Phase P2 in the Phase 2 roadmap (`roadmap-phase2.md`):

- **P0** — Foundation blockers (Issue A, T-02 atomic rollback, T-01 path hardening, T-07 exception hierarchy)
- **P1** — Issue Contracts (JSON schemas, structured task specifications, schema-pinned agent prompts)
- **P2** — **Dogfooding** (clone workflow, self-scans, self-plans, self-fixes)
- **P3** — Async Workers (Docker, CI/CD, auto-PR workflow)
- **P4** — Defense in Depth (test-zero codebase solutions, mutation testing, fuzzing)
- **P5** — Autonomy Tuning (budget-aware architect, priority gates, human-in-loop policies)

**Key decision:** Async Workers (P3) was prioritized over Defense in Depth (P4) because CI/CD infrastructure is a prerequisite for any test-zero defense, not a competitor. An isolated Docker environment provides the runtime for all subsequent defenses.

## Prerequisites

Before dogfooding can begin, all P0 blockers must be implemented:

1. **Issue A** — Structured Contract Parsing: `parse_llm_response()` with 11 ACs, exception hierarchy, positional extraction
2. **T-02** — Atomic rollback: extract `force_reset_apply` as reusable primitive (from `main.py:apply`)
3. **T-01** — Path traversal hardening: contract enforcement in `workspace.py` and `executor.py`
4. **T-07** — Exception hierarchy + circuit breaker: typed failure isolation for LLM providers

## The "Clone" Workflow

To avoid circular dependencies and protect the live repository, the system uses a cloning strategy:

1. **Anchor**: Clone the repository at a fixed commit/tag (e.g., `v1.0.0`).
2. **Isolate**: Use a dedicated external workspace for the clone's artifacts.
3. **Execute**: Run `patchforge plan --issue-file issue.md` against the clone.
4. **Validate**: Execute Ruff and Pytest within the clone's environment.
5. **Review**: A human reviews the generated `patch.diff`.
6. **Promote**: If approved, the change is applied to the original repository.

## Why this is safe

- **No Magic**: The system doesn't "guess" how to improve; it follows a human-written contract (the issue).
- **Isolation**: A catastrophic failure in the `preview` or `apply` phase only affects the temporary clone. Docker containers (P3) will further isolate the runtime.
- **Objective Truth**: Success is measured by existing tests and linters, not by the LLM's own opinion of the code.
- **Controlled Evolution**: The tool is only allowed to evolve through the same pipeline it provides to its users.
- **Round-trip stability**: Inter-stage schemas are verified by CI tests, ensuring that persistence does not silently corrupt stage transitions.

## Deferred: Formalization of "Experiment" as a Schema Concept

The concept of **"experiment"** as the carrier of execution context — commit SHA, repository identity, workspace path — exists only in planning documents (`roadmap-phase2.md`, `dogfooding-vision.md`). It is not formalized in schemas or code.

This is a deliberate deferral: Invariant #9 establishes that inter-stage DTOs are the unit of **specification**, not the unit of **context**. The experiment layer (which binds a DTO to its execution environment) is a separate architectural concern that will need to be formalized before P3 (Async Workers / CI/CD).

**Prerequisite for P3:** Before CI/CD workers can operate, the system must have a formal `Experiment` schema that associates:
- A plan DTO (`plan.json` specification)
- A target commit SHA
- A repository identity (clone URL or path)
- A workspace path
- A run ID for audit trail

Without this formalization, P3 workers cannot verify that a plan loaded from disk was generated for the same commit they are about to execute against. The architecture acknowledges this gap and defers it to P2/P3 design work.

## Success Metrics for Experiments

A self-improvement experiment is successful if:
1. The generated patch satisfies all ACs of the issue.
2. The patch is accepted by a human reviewer without significant modification.
3. All existing tests pass after application.
4. No new linting violations are introduced.
5. The pipeline did not abort with a P0-class error (uncaught parse failure, rollback failure, path traversal, untyped exception).
