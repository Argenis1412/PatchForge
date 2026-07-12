# P4 — 4. Audit Bundle Export

> **Source of truth:** `docs/planning/roadmap.md` §P4-4 (idea 7)
> **Status:** 📐 Scoped (not yet opened as GitHub issue)
> ⚠️ This doc is an implementation guide. Verify file paths and function signatures against the codebase before coding. The roadmap is authoritative for goal, effort, and cuts.

## Context

No tool in this space produces a compliance-grade export of a run's provenance. This item adds `patchforge export-audit <run_id>`, producing a tarball + manifest with SHA-256 of every artifact, PatchForge version, `schema_version`, providers used, and `commit_anchor`. Turns the "auditable artifacts" property already built into the pipeline (per Invariant #3 in `CONTEXT.md`) into an exportable, verifiable compliance deliverable.

## Scope

- New CLI command `patchforge export-audit <run_id>` → `audit-<run_id>.tar.gz` + `manifest.json`.
- Manifest: **structural mirror of `RunMetadata`** (embeds full `RunMetadata.model_dump()` output — see architectural note below) + bundle-native metadata: SHA-256 of every artifact in `runs/<run_id>/`, PatchForge version, `commit_anchor`, UTC timestamp.
- Optional GPG signing.

**Precondition on run state (audit-grade property):** `export-audit` rejects runs whose `RunMetadata.lifecycle_state` is not terminal. Terminal candidates: `applied`, `failed`, `rolled_back` (final list decided at Clarifier). Non-terminal runs exit with a specific error code. **No repo lock is acquired** — Invariant #3 already guarantees per-artifact atomicity via WAL; the terminality precondition is what closes the gap for "audit-grade" (a run mid-flight can still have artifacts being rewritten between the first and last read of the export). This is the contract that makes the audit-grade property enforceable rather than aspirational.

See `roadmap.md` §P4-4 for the full Goal/Impact/Cuts text.

## Architectural note — manifest is a structural mirror, not an enumerated schema

The manifest embeds the full `RunMetadata.model_dump()` output rather than enumerating individual RunMetadata fields. This is a deliberate composition choice:

- **Composition with Item 05:** Item 05 (Approval Provenance) will add `triggered_by` / `approved_by` to `RunMetadata` as additive fields per ADR-0004. If the manifest enumerated fields, Item 04 completing first would freeze a schema that silently omits provenance. The mirror pattern makes composition order-independent — any additive field to `RunMetadata` propagates to the manifest automatically.
- **Versioning:** The RunMetadata mirror adopts ADR-0004's versioning through `schema_version` on `RunMetadata` itself. The manifest wrapper has its own metadata (SHAs, timestamp, PatchForge version) but does not attempt to version the embedded RunMetadata dump.
- **Contract:** The manifest is a terminal derived artifact, not an inter-stage schema — Invariant #2 (round-trip stability for cross-stage DTOs) does not apply. Consumers of the manifest read it, verify it, and archive it; they never feed it back into the pipeline.

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

1. Confirm item 3 (Provider Registry) has landed — the manifest's `RunMetadata` mirror will include the per-role model tracking automatically (see architectural note above).
2. New CLI command `patchforge export-audit <run_id>` in `commands/export_audit.py`.
3. Check `RunMetadata.lifecycle_state` for the target run — abort with a specific error code if it is not terminal (see precondition in Scope).
4. Define the manifest wrapper: bundle-native metadata (SHA-256 of every artifact in `runs/<run_id>/`, PatchForge version, `commit_anchor`, UTC timestamp) + full `RunMetadata.model_dump()` embed. **Do not** enumerate individual RunMetadata fields — the mirror pattern is the point.
5. Produce `audit-<run_id>.tar.gz` containing the run's artifacts + `manifest.json`.
6. Optional `--sign` flag: GPG detached signature on `manifest.json`.
7. Implement verification per the resolved open question above (flag or separate command) — recomputes hashes and reports mismatches.
8. Tests: bundle round-trip (export → verify succeeds), tamper detection (mutate an artifact, verify fails), missing artifact detection, non-terminal run rejection.

**PR body coordination (composition with Item 05):** If Item 4 lands before Item 5 and this item needs to modify the PR body (e.g., add a link to the audit bundle), consolidate PR body construction into `GitHubClient.create_pr()` (`clients/github.py:90`) as a single choke point *here* — the three current callers (`.github/workflows/patchforge-pipeline.yml`, `src/orchestrator/storage/work_queue.py:424`, any other) must pass structured data, not body strings. The same rule applies to any P4/P5 item touching PR body output — whoever arrives first pays the refactor.

## Branch & commit

Branch: `⚠️ REPLACE XXX with GitHub issue number` → `feat/issue-XXX-audit-bundle-export`
Suggested commit prefix: `feat(cli): …`
Commit granularity decided at pickup — one logical change per commit.

## Acceptance criteria (placeholder)

Full ACs written in GitHub issue at pickup time (Clarifier → AC Challenger → Adversarial Reviewer flow).
Minimum bar before merge:
- Roadmap Cuts respected (local export only, no chain of custody, no RFC 3161).
- Item 3 (Provider Registry) confirmed complete before this item starts.
- Manifest embeds full `RunMetadata.model_dump()` (structural mirror), does NOT enumerate individual fields.
- `export-audit` rejects non-terminal runs with a specific error code.
- If this item touches PR body output before Item 05 lands, PR body is consolidated into `GitHubClient.create_pr()` (choke-point refactor).
- QA gate green (`ruff check .` + `ruff format --check .` + `pytest`).
- Tests added for the behavioral change (per Workflow.md testing table).
- `docs/context/CONTEXT.md` "Completed" section updated in same PR.
- `docs/planning/issue-registry.md` status flipped to ✅ Completed with PR link.
