# P4 — 1. Qualitative Risk Gates

> **Source of truth:** `docs/planning/roadmap.md` §P4-1 (idea 2)
> **Status:** 📐 Scoped (not yet opened as GitHub issue)
> ⚠️ This doc is an implementation guide. Verify file paths and function signatures against the codebase before coding. The roadmap is authoritative for goal, effort, and cuts.

## Context

`check_plan_gate()` in `risk.py` currently gates on `DANGEROUS_PATTERNS` — pattern matching, not semantic understanding of what a change touches. This item extends the gate with a file-semantic taxonomy (`schemas/*` = HIGH risk, `tests/*` = LOW risk, etc.) so risk classification reflects what kind of file is being modified, not just whether its diff matches a dangerous regex. Closes the gap between "counting diff lines" and "understanding what is being touched" — a direct input to the "trust layer" thesis.

## Scope

- File-semantic taxonomy: a mapping from path patterns to risk tiers (HIGH/MEDIUM/LOW), configurable. The taxonomy is only active when configuration is explicitly provided — there are no hardcoded defaults that fire without user opt-in.
- `check_plan_gate()` consults the taxonomy in addition to `DANGEROUS_PATTERNS` — additive, not a replacement.
- Backward compatibility: no taxonomy config present → no taxonomy classification runs → gate behavior is byte-identical to current.

See `roadmap.md` §P4-1 for the full Goal/Impact/Cuts text.

## Non-goals / Cuts

- No `pipeline.py` changes (roadmap: "touches `risk.py`... but not `pipeline.py`").
- No ADR required (roadmap: "No ADR needed").
- No semantic code interpretation (that's Scout territory, not Core — see `roadmap.md` "Two Product Lines").
- No auto-merge or auto-apply execution change — however, taxonomy escalation mutates `task.risk_level` and adds failure reasons via `check_plan_gate()`, which can change gate outcomes and downstream lifecycle behavior. This item does not modify `compute_auto_apply_eligible()` itself, but elevated risk_levels flow through to it indirectly.

## Open questions

- Does the taxonomy live as a config-driven mapping (in `orchestrator.json`) or as hardcoded constants in `risk.py` with the roadmap's two examples as the only entries? The roadmap doesn't specify; resolve during Clarifier.
- How does an escalated `risk_level` interact with an already-set `risk_level` from `DANGEROUS_PATTERNS`? Additive (take the max) is the natural default — confirm during AC Challenger.

## Preconditions

None. Extends the `auto_apply_eligible` mechanism from Issue #198 (`compute_auto_apply_eligible()` in `artifacts.py`) but does not require changes to it — see data flow note below.

## Files likely to be touched

| File | Change type |
|---|---|
| `src/orchestrator/risk.py` | EDIT — add taxonomy constants + extend `check_plan_gate()` |
| `tests/test_risk*.py` (exact file TBD — grep for existing risk tests) | EDIT — new taxonomy test cases |

## Data flow (important — do not misroute)

The taxonomy feeds into `check_plan_gate()` (`risk.py:62`), which classifies **task-level** risk during planning. It does **not** feed directly into `compute_auto_apply_eligible()` (`artifacts.py:105`), which computes a **run-level** eligibility flag from `risk_budget`, `lifecycle_state`, and `executor_had_errors` — none of which this item changes directly. The connection is indirect: if the taxonomy escalates a task's `risk_level`, that can influence the `risk_budget` gate upstream of `compute_auto_apply_eligible()`, but this item does not touch `compute_auto_apply_eligible()`'s signature.

## Implementation steps

1. Read `check_plan_gate()` (`risk.py:62`) in full — confirm current `DANGEROUS_PATTERNS` usage and return type before adding to it.
2. Add file-semantic taxonomy constants (`schemas/*` = HIGH, `tests/*` = LOW, and any additional tiers agreed during Clarifier) in `risk.py`.
3. Extend `check_plan_gate()` to consult the taxonomy alongside `DANGEROUS_PATTERNS`, escalating task `risk_level` when a touched path matches a HIGH-tier pattern.
4. Backward-compat guard: verify that with no taxonomy config, gate output is identical to current behavior (add a regression test asserting this).
5. Tests: 3+ taxonomy match cases (HIGH, MEDIUM, LOW), 1 backward-compat case (no taxonomy → unchanged), 1 case proving `DANGEROUS_PATTERNS` and taxonomy compose correctly (e.g., taxonomy LOW + dangerous pattern match still escalates).

## Branch & commit

Branch: `⚠️ REPLACE XXX with GitHub issue number` → `feat/issue-XXX-qualitative-risk-gates`
Suggested commit prefix: `feat(risk): …`
Commit granularity decided at pickup — one logical change per commit.

## Acceptance criteria (placeholder)

Full ACs written in GitHub issue at pickup time (Clarifier → AC Challenger → Adversarial Reviewer flow).
Minimum bar before merge:
- Roadmap Cuts respected (no `pipeline.py` change, no ADR).
- QA gate green (`ruff check .` + `ruff format --check .` + `pytest`).
- Tests added for the behavioral change (per Workflow.md testing table).
- `docs/context/CONTEXT.md` "Completed" section updated in same PR.
- `docs/planning/issue-registry.md` status flipped to ✅ Completed with PR link.
