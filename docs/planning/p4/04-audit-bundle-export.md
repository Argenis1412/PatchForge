# P4 — 4. Audit Bundle Export

> **Source of truth:** `docs/planning/roadmap.md` §P4-4 (idea 7)
> **Status:** 📐 Scoped (not yet opened as GitHub issue)
> ⚠️ This doc is an implementation guide. Verify file paths and function signatures against the codebase before coding. The roadmap is authoritative for goal, effort, and cuts.

## Context

No tool in this space produces a compliance-grade export of a run's provenance. This item adds `patchforge export-audit <run_id>`, producing a tarball + manifest with SHA-256 of every artifact, PatchForge version, `schema_version`, providers used, and `commit_anchor`. Turns the "auditable artifacts" property already built into the pipeline (per Invariant #3 in `CONTEXT.md`) into an exportable, verifiable compliance deliverable.

## Scope

- New CLI command `patchforge export-audit <run_id>` → `audit-<run_id>.tar.gz` + `manifest.json`.
- Manifest: SHA-256 of every artifact in `runs/<run_id>/`, PatchForge version, `schema_version`, providers used per role, `commit_anchor`, UTC timestamp.
- Optional GPG signing.

See `roadmap.md` §P4-4 for the full Goal/Impact/Cuts text.

## Non-goals / Cuts

- Local export only — no upload to S3 or artifact registries (roadmap Cuts).
- No multi-run chain of custody.
- No RFC 3161 timestamping.
- No new invariants — this is a new CLI command in `commands/`, orchestration and business-logic boundaries (per CONTEXT.md Invariants #1, #4) are unaffected.

## Open questions

- **`--verify` command shape:** should verification be a flag on `export-audit` (`export-audit --verify <bundle>`) or a separate command (`patchforge verify-audit <bundle>`)? Export and verify are distinct responsibilities — export produces a new artifact, verify only reads and checks an existing one. Resolve during Clarifier; a separate command is the more conventional CLI shape but either is workable.
- Exact manifest field for "providers used per role" depends on how item 3 (Provider Registry) ends up recording this on `RunMetadata` — confirm the field name once item 3 lands.

## Preconditions

**Item 3 (Provider Registry) must be complete.** Per `docs/planning/issue-registry.md` (P4 entry for Audit Bundle Export): "audit manifest must record the exact model used." Without item 3, there is no per-role model field on `RunMetadata` to read into the manifest — this is a hard precondition, not a soft one.

## Files likely to be touched

| File | Change type |
|---|---|
| `src/orchestrator/commands/export_audit.py` | CREATE |
| `src/orchestrator/main.py` | EDIT — register the new CLI command (CLI surface only, per Invariant #4) |
| Manifest schema (new file, e.g. `schemas/audit_manifest.py`) | CREATE |
| Tests for `export_audit` (new file, e.g. `tests/test_export_audit.py`) | CREATE |

## Implementation steps

1. Confirm item 3 (Provider Registry) has landed and identify the exact `RunMetadata` field(s) it added for per-role model tracking.
2. New CLI command `patchforge export-audit <run_id>` in `commands/export_audit.py`.
3. Define the manifest schema: SHA-256 of every artifact in `runs/<run_id>/` + PatchForge version + `schema_version` + providers used (read from item 3's `RunMetadata` fields) + `commit_anchor` + UTC timestamp.
4. Produce `audit-<run_id>.tar.gz` containing the run's artifacts + `manifest.json`.
5. Optional `--sign` flag: GPG detached signature on `manifest.json`.
6. Implement verification per the resolved open question above (flag or separate command) — recomputes hashes and reports mismatches.
7. Tests: bundle round-trip (export → verify succeeds), tamper detection (mutate an artifact, verify fails), missing artifact detection.

## Branch & commit

Branch: `⚠️ REPLACE XXX with GitHub issue number` → `feat/issue-XXX-audit-bundle-export`
Suggested commit prefix: `feat(cli): …`
Commit granularity decided at pickup — one logical change per commit.

## Acceptance criteria (placeholder)

Full ACs written in GitHub issue at pickup time (Clarifier → AC Challenger → Adversarial Reviewer flow).
Minimum bar before merge:
- Roadmap Cuts respected (local export only, no chain of custody, no RFC 3161).
- Item 3 (Provider Registry) confirmed complete before this item starts.
- QA gate green (`ruff check .` + `ruff format --check .` + `pytest`).
- Tests added for the behavioral change (per Workflow.md testing table).
- `docs/context/CONTEXT.md` "Completed" section updated in same PR.
- `docs/planning/issue-registry.md` status flipped to ✅ Completed with PR link.
