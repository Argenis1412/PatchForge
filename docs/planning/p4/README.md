# P4 — Trust & Configuration — Planning Docs

Per-item implementation guides for the five P4 items. See [`../roadmap.md`](../roadmap.md) §P4 for the strategic theme, impact rationale, and cuts — these docs do not restate it, they extend it into actionable scope.

## Items

| # | Title | Difficulty | Effort | Preconditions | Doc |
|---|---|---|---|---|---|
| 1 | Qualitative Risk Gates | MEDIUM | 3–5 d | None (extends #198) | [01-qualitative-risk-gates.md](01-qualitative-risk-gates.md) |
| 2 | IssueContract ADR | LOW | 2–3 d | None | [02-issue-contract-adr.md](02-issue-contract-adr.md) |
| 3 | Provider Registry | MEDIUM | 2–4 d | None | [03-provider-registry.md](03-provider-registry.md) |
| 4 | Audit Bundle Export | MEDIUM | 3–5 d | **Item 3 complete** | [04-audit-bundle-export.md](04-audit-bundle-export.md) |
| 5 | Approval Provenance | MEDIUM | 3–5 d | None | [05-approval-provenance.md](05-approval-provenance.md) |

## Status tracking

Live status for each item lives in [`../issue-registry.md`](../issue-registry.md) (📐 Scoped → ✅ Completed). This folder does not track status — it's a static implementation guide, refreshed only when an item's approach changes materially.

## How to use these docs

Full acceptance criteria, non-goals, and exit conditions are written when an item becomes a GitHub issue, following the Clarifier → AC Challenger → Adversarial Reviewer flow described in [`../../context/Workflow.md`](../../context/Workflow.md). These docs are the starting point for that flow, not a substitute for it.

Each item doc includes an **Open questions** section — resolve those during the Clarifier step, not by guessing from this doc alone.

## When P5 begins

The same template applies. A `docs/planning/p5/` folder should be created following this pattern when P5 items are picked up.
