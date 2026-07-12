# P4 — 5. Approval Provenance

> **Source of truth:** `docs/planning/roadmap.md` §P4-5 (idea 10)
> **Status:** 📐 Scoped (not yet opened as GitHub issue)
> ⚠️ This doc is an implementation guide. Verify file paths and function signatures against the codebase before coding. The roadmap is authoritative for goal, effort, and cuts.

## Context

The pipeline already requires a human gate before any patch is applied (per the product thesis). This item formalizes *who* triggered and *who* approved a run as two additive `RunMetadata` fields — `triggered_by` and `approved_by` — captured from `github.actor` in CI and `git config user.*` locally, surfaced in the PR body. Important for separation-of-duties in enterprise contexts.

## Scope

- Two additive fields on `RunMetadata`: `triggered_by: str | None`, `approved_by: str | None`.
- Capture path A (CI): `github.actor` when running under `patchforge ci`.
- Capture path B (local): `git config user.name` / `git config user.email`.
- Provenance line surfaced in every PR body the pipeline generates — **there are multiple such locations, see Files below.**

See `roadmap.md` §P4-5 for the full Goal/Impact/Cuts text.

## Non-goals / Cuts

- No authorization policy or role checks — this is a record, not a gate (roadmap: "Record only, not policy").
- No multi-person approval flow.
- No cryptographic identity verification — GPG on commits already covers that per CONTEXT.md Invariant #6.
- Additive with default → no `schema_version` bump, per ADR-0004 (field addition with default = additive, not breaking).

## Open questions

- None outstanding — this item is well-specified by the roadmap. The main risk is incomplete implementation (missing one of the PR-body call sites below), not ambiguity of intent.

## Preconditions

**Pre-work required inside this item (Step 0 — see Implementation steps):** consolidate PR body construction into `GitHubClient.create_pr()` (`clients/github.py:90`) as a single choke point. This refactor is a precondition of Item 5 rather than a separate item. Rationale: without a choke point, provenance injection has to be duplicated across three call sites (`.github/workflows/patchforge-pipeline.yml`, `src/orchestrator/storage/work_queue.py:424`, and future callers), and Item 4 (Audit Bundle) has the same shape of work. Consolidating once here eliminates order-dependence with Item 4.

Otherwise independent — no other hard dependencies.

## Architectural note — capture vs. domain logic (Invariants #1/#4/#5)

The capture of "actor" values has two levels that must live in different modules:

- **Level 1 — raw reads** (pure command wrappers, no provenance semantics):
  - `git config user.name` / `git config user.email` → can be hosted in `src/orchestrator/git.py` (Invariant #5: `git.py` is a pure command wrapper).
  - `github.actor` env var read → inline in the caller (CI entry point).
- **Level 2 — domain logic** (mapping to `triggered_by` / `approved_by`, source selection between CI env and local, fallback rules): lives in a **new domain module** `src/orchestrator/provenance.py`. This is where "what does this value mean" is decided.

**Explicitly prohibited locations for Level-2 domain logic:**
- `git.py` — would violate Invariant #5 (pure command wrapper, no domain logic).
- `pipeline.py` — would violate Invariant #1 (orchestration only, no business logic).
- `main.py` — would violate Invariant #4 (CLI surface only).

No ADR is required — this is application of existing invariants, not modification. The final module name (`provenance.py` is the candidate; alternatives are permitted if they preserve the two-level split) is decided at Clarifier.

## Files likely to be touched

All PR body construction is consolidated into the choke point (Step 0). After that, `RunMetadata` and the new domain module are the only additional edits.

| File | Change type | Confirmed detail |
|---|---|---|
| `src/orchestrator/clients/github.py` | EDIT — Step 0 choke point: `create_pr()` builds the body from structured data | `create_pr(title, body, head, base)` at line 90 today receives a pre-built body; the refactor moves body construction into this method |
| `.github/workflows/patchforge-pipeline.yml` | EDIT — pass structured data (no body string) | Currently builds body at lines ~150–168 |
| `src/orchestrator/storage/work_queue.py` | EDIT — pass structured data (no body string) | Currently at line 424: `github.create_pr(body=f"...")` |
| `src/orchestrator/schemas/artifacts.py` | EDIT — add `triggered_by`, `approved_by` fields to `RunMetadata` | Additive fields with default `None`, no `schema_version` bump per ADR-0004 |
| `src/orchestrator/provenance.py` (new) | CREATE — Level-2 domain module: source selection, mapping to `triggered_by`/`approved_by`, fallback | Do not merge into `git.py`/`pipeline.py`/`main.py` |
| `src/orchestrator/git.py` | EDIT (optional, small) — Level-1 wrappers for `git config user.name` / `user.email` reads if not already present | Pure command wrapper, no provenance semantics inside `git.py` |

## Implementation steps

**Step 0 (precondition, done inside this item):** Consolidate PR body construction into `GitHubClient.create_pr()` (`clients/github.py:90`) as a single choke point. The three current callers (`.github/workflows/patchforge-pipeline.yml`, `src/orchestrator/storage/work_queue.py:424`, any other discovered via `grep -rE "create_pr|pr create"`) pass structured data (title + fields) to `create_pr()`, which builds the body internally. All subsequent steps assume this consolidation is complete. **Do this first** — the rest of the work is much smaller once the choke point exists.

1. Add `triggered_by: str | None = None` and `approved_by: str | None = None` to `RunMetadata` (`schemas/artifacts.py`) — additive, default `None`, no `schema_version` bump per ADR-0004.
2. Create the domain module `src/orchestrator/provenance.py` with the Level-2 logic (see architectural note above): source selection between CI env and local, mapping to `triggered_by`/`approved_by`, fallback rules. This module has no dependencies on `git.py` internals beyond calling the Level-1 wrappers.
3. Capture path A (CI): read `github.actor` from the environment inline in the CI entry point, hand it to `provenance.py`.
4. Capture path B (local): read `git config user.name` and `git config user.email` via `git.py` wrappers (add them if absent — they are pure command wrappers, no provenance semantics inside `git.py`). Hand the raw values to `provenance.py`.
5. Wire the `provenance.py` output into the choke point (`GitHubClient.create_pr()`) so the provenance line appears in every PR body regardless of caller.
6. Tests: capture path A (mocked CI env), capture path B (mocked `git config`), round-trip stability on `RunMetadata`, PR body contains the provenance line for both call sites (workflow `.yml` and worker loop), no Level-2 domain logic reachable from `git.py`/`pipeline.py`/`main.py`.

## Branch & commit

Branch: `⚠️ REPLACE XXX with GitHub issue number` → `feat/issue-XXX-approval-provenance`
Suggested commit prefix: `feat(schemas): …`
Commit granularity decided at pickup — one logical change per commit.

## Acceptance criteria (placeholder)

Full ACs written in GitHub issue at pickup time (Clarifier → AC Challenger → Adversarial Reviewer flow).
Minimum bar before merge:
- Roadmap Cuts respected (record only, no policy, no crypto verification).
- Step 0 done: PR body consolidated into `GitHubClient.create_pr()`; three callers pass structured data, not body strings.
- Level-2 domain logic lives in `provenance.py` (or equivalent module decided at Clarifier). No provenance semantics inside `git.py`, `pipeline.py`, or `main.py`.
- Provenance line verified present in PR bodies from **both** the CI workflow path and the worker-loop path.
- QA gate green (`ruff check .` + `ruff format --check .` + `pytest`).
- Tests added for the behavioral change (per Workflow.md testing table).
- `docs/context/CONTEXT.md` "Completed" section updated in same PR.
- `docs/planning/issue-registry.md` status flipped to ✅ Completed with PR link.
