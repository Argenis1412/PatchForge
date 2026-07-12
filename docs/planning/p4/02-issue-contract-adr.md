# P4 — 2. IssueContract ADR

> **Source of truth:** `docs/planning/roadmap.md` §P4-2 (idea 6)
> **Status:** 📐 Scoped (not yet opened as GitHub issue)
> ⚠️ This doc is an implementation guide. Verify file paths and function signatures against the codebase before coding. The roadmap is authoritative for goal, effort, and cuts.

## Context

Today issues reach the pipeline through `IssueInput` (`schemas/issue.py`), parsed from human-written markdown via `--issue-file`. The roadmap anticipates two more sources arriving later: GitHub API issues (adapter, future issue) and Scout (a separate product line — see `scout-vision.md`). `IssueContract` is meant to be the canonical representation across all three sources, decided now via ADR-0005 so a costly refactor isn't needed when Scout arrives. This issue writes the ADR + schema only — no consumer wiring.

## Scope

- ADR-0005 decision document: canonical `IssueContract` shape, why it's needed now, alternatives considered.
- `IssueContract` schema in `schemas/issue.py` — pure DTO, round-trip stable, no pipeline consumers changed.

See `roadmap.md` §P4-2 for the full Goal/Impact/Cuts text.

## Non-goals / Cuts

- No adapter implementation (GitHub Issue → IssueContract) — that's a separate future issue per roadmap.
- No Scout code.
- No pipeline consumption — zero consumer wiring in this issue (roadmap: "Zero pipeline code").

## Open questions

Resolve these during the Clarifier step before writing the ADR — do not pre-decide from this doc:

- **Relationship to `IssueInput`:** does `IssueContract` subsume `IssueInput`, compose it, or coexist alongside it as a separate abstraction layer? `IssueInput` is markdown-frontmatter-specific (title, severity, labels, body, raw); `IssueContract` is meant to be source-agnostic. The ADR must decide this explicitly.
- **Versioning:** ADR-0004 restricts `schema_version: int` to `RunMetadata` only — intermediate/inter-stage schemas are explicitly out of scope for that field (see `docs/context/CONTEXT.md` Invariant #3, and ADR-0004 §2 "Which schemas carry it"). `IssueContract` does **not** get a `schema_version` field unless ADR-0005 explicitly extends ADR-0004's scope — which is itself a decision the ADR must make consciously, not by default.
- **Source-neutrality (mandatory constraint for ADR-0005):** `IssueContract` must achieve representational completeness across markdown / GitHub API / Scout **without encoding source in the DTO**. This is required to preserve Invariant #9 (meaning equals representation; provenance and meaning are orthogonal — see CONTEXT.md L206).
  - **Prohibited:** any field like `source: Literal["markdown", "github", "scout"]` or any equivalent origin discriminator. A `source` field would make the same DTO shape mean different things depending on which producer emitted it — precisely what Invariant #9 forbids.
  - **Permitted:** optional fields whose semantics is "this datum may not exist for this work item". **Not permitted:** optional fields whose semantics is "this datum absent because it came from source X".
  - **Effort caveat:** Roadmap estimates 2–3d. Re-estimate at issue opening if the source-neutrality resolution forces more than one hosted schema (e.g., adapter types outside `IssueContract` itself).

## Preconditions

None.

## Files likely to be touched

| File | Change type |
|---|---|
| `docs/adr/ADR-0005-issue-contract.md` | CREATE |
| `src/orchestrator/schemas/issue.py` | EDIT — add `IssueContract` alongside existing `IssueInput` |
| `tests/test_issue_schema.py` | EDIT — round-trip stability test |
| `docs/index.md` | EDIT — add ADR-0005 to Decision Records table |

## Implementation steps

1. Resolve the open questions above (IssueContract/IssueInput relationship; versioning scope) — this is the substance of the ADR, not a pre-step to skip.
2. Draft `docs/adr/ADR-0005-issue-contract.md` following the ADR-0004 structure (Context, Decision, Consequences, Rejected alternatives, Known debt).
3. Add `IssueContract` schema in `schemas/issue.py`. Pure DTO — no `schema_version` field unless the ADR explicitly decided otherwise in step 1.
4. Round-trip stability test in `tests/test_issue_schema.py`: `IssueContract.model_validate_json(m.model_dump_json()) == m` for a validly-constructed instance (per Invariant #2 in CONTEXT.md).
5. Update `docs/index.md` Decision Records table with the ADR-0005 entry.
6. Confirm zero pipeline consumer wiring — no other file should import or reference `IssueContract` yet.

## Branch & commit

Branch: `⚠️ REPLACE XXX with GitHub issue number` → `feat/issue-XXX-issue-contract-adr`
Suggested commit prefix: `docs(adr): …` for the ADR, `feat(schemas): …` for the schema — granularity (one commit or two) decided at pickup.

## Acceptance criteria (placeholder)

Full ACs written in GitHub issue at pickup time (Clarifier → AC Challenger → Adversarial Reviewer flow).
Minimum bar before merge:
- Roadmap Cuts respected (no adapter, no Scout code, no consumer wiring).
- ADR explicitly resolves the IssueContract/IssueInput relationship and the versioning scope question.
- QA gate green (`ruff check .` + `ruff format --check .` + `pytest`).
- Round-trip stability test passes.
- `docs/context/CONTEXT.md` "Completed" section updated in same PR.
- `docs/planning/issue-registry.md` status flipped to ✅ Completed with PR link.
