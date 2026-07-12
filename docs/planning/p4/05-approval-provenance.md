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

None (independent). Strong synergy with item 4 (Audit Bundle Export) — the manifest is a natural place to also surface provenance — but not a hard dependency in either direction.

## Files likely to be touched — ALL PR-body construction sites

There are **three** confirmed places that construct a PR body or title in this codebase. All three must be updated, not just one — missing any of them means worker-generated or CI-generated PRs silently lack provenance.

| File | Change type | Confirmed detail |
|---|---|---|
| `src/orchestrator/schemas/artifacts.py` | EDIT — add `triggered_by`, `approved_by` fields to `RunMetadata` | — |
| `.github/workflows/patchforge-pipeline.yml` | EDIT — `gh pr create` body construction | Confirmed at lines ~150–168 |
| `src/orchestrator/storage/work_queue.py` | EDIT — headless worker loop calls `github.create_pr(...)` | Confirmed at line 424 |
| `src/orchestrator/clients/github.py` | Reference only — `create_pr(title, body, head, base)` at line 90 is the shared client method both callers above route through | Confirm whether provenance should be injected here once, or at each caller |
| Capture logic (new helper, exact location TBD — likely `git.py` for local capture, CI env read inline) | CREATE/EDIT | `git.py` is a pure command wrapper (Invariant #5) — a `git config user.name`/`user.email` read fits there |

## Implementation steps

1. Add `triggered_by: str | None = None` and `approved_by: str | None = None` to `RunMetadata` (`schemas/artifacts.py`) — additive, default `None`, no `schema_version` bump per ADR-0004.
2. Capture path A (CI): read `github.actor` from the environment when running under `patchforge ci`.
3. Capture path B (local): read `git config user.name` and `git config user.email` (two separate config keys — not one).
4. Check whether provenance injection belongs in the shared `GitHubClient.create_pr()` (`clients/github.py:90`) — a single choke point — or must be duplicated at both call sites (`patchforge-pipeline.yml` and `work_queue.py:424`). Prefer the choke point if the body-construction responsibility already lives there; otherwise update both callers explicitly.
5. Verify the provenance line appears in PR bodies from **both** the GitHub Actions path and the headless worker-loop path — these are functionally independent flows and must both be tested.
6. Tests: capture path A (mocked CI env), capture path B (mocked `git config`), round-trip stability on `RunMetadata`, PR body contains the provenance line for both call sites.

## Branch & commit

Branch: `⚠️ REPLACE XXX with GitHub issue number` → `feat/issue-XXX-approval-provenance`
Suggested commit prefix: `feat(schemas): …`
Commit granularity decided at pickup — one logical change per commit.

## Acceptance criteria (placeholder)

Full ACs written in GitHub issue at pickup time (Clarifier → AC Challenger → Adversarial Reviewer flow).
Minimum bar before merge:
- Roadmap Cuts respected (record only, no policy, no crypto verification).
- Provenance line verified present in PR bodies from **both** the CI workflow path and the worker-loop path.
- QA gate green (`ruff check .` + `ruff format --check .` + `pytest`).
- Tests added for the behavioral change (per Workflow.md testing table).
- `docs/context/CONTEXT.md` "Completed" section updated in same PR.
- `docs/planning/issue-registry.md` status flipped to ✅ Completed with PR link.
