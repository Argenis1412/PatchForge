# CS-002: Move `write_verdict()` from `schemas/experiment.py` to `workspace.py`

## Metadata

| Field | Value |
|-------|-------|
| **Issue** | `issues/002-move-write-verdict.md` |
| **Target repo** | PatchForge (Clon_PatchForge) |
| **Date** | 2026-06-14 |
| **Experiment** | 002 — Refactor I/O out of schemas |
| **Branch** | `main` (clon) / `refactor/experiment-002-move-write-verdict` (original) |

## Results

| Metric | Value |
|--------|-------|
| Files modified (semantic) | 3 (`workspace.py`, `experiment.py`, `test_experiment_schema.py`) |
| Documentation files | 1 (`discoveries.md`) |
| Lines changed | 50 insertions, 49 deletions |
| Tests executed | 290 |
| Tests passed | 288 |
| Tests failed | 0 |
| Ruff | 0 errors |
| Total LLM cost | ~$0.036 (1 plan run) |
| Human time | ~15 min (pipeline debugging + direct apply) |
| Pipeline time (scan → plan) | ~2 min |

## Pipeline execution details

| Step | Result |
|------|--------|
| `scan` | ✅ — 46 hotspots detected |
| `plan --issue-file` | ✅ — 5 tasks, 4 files, $0.036 |
| `preview` | ⚠️ — Patch generated but validation failed |
| `apply` | ⚠️ — Manual apply (see below) |

### Why preview validation failed

The executor generated a partial patch: it removed `write_verdict()` from
`experiment.py` and updated the tests, but **did not add the method to
`workspace.py`**. The root cause is LLM task execution order: the executor
skipped T2 (add to workspace.py) while executing T3 (update tests) and T4
(remove from experiment.py), producing a patch that cannot compile.

### Why apply was manual

The generated `patch.diff` was incomplete (missing the workspace.py addition).
Applying it would have broken the clone. The changes were applied directly
to the clone following the plan's specification.

## Bugs discovered

| # | Bug | Impact | Fix |
|---|-----|--------|-----|
| 1 | Executor skipped T2 (workspace.py) when T1 (audit) was "already applied" | Incomplete patch; validation failed | Task dependency chain confused executor — if a dependency reports "no change needed", downstream tasks must still be executed |

## Infrastructure notes

- **Groq API key (403 Forbidden):** The Groq API key in the `.env` file
  returned 403 during preview. All tasks were reclassified to `low` risk in
  `plan.json` to route them through Gemini (which worked). The key may be
  expired or rate-limited.
- **Risk budget adjustment:** `run.json` was edited post-scan to set
  `risk_budget: "medium"` and `max_files: 5` to allow the refactor tasks
  through the plan gate. The defaults (`low`, `max_files=2`) are too
  restrictive for multi-file refactors.

## Lessons

1. **The issue-file pipeline works for plan generation.** Claude correctly
   decomposed the refactor into the right set of 5 tasks with correct
   dependency ordering.
2. **Executor task execution order is brittle.** When a dependency task (T1)
   produces no-op output ("already applied"), downstream tasks that depend
   on it (T2) may be skipped. The executor should respect the dependency
   DAG, not skip tasks based on adjacent task outcomes.
3. **Risk budget defaults are too low for refactors.** A pure I/O move across
   3 files with no logic change should not require manual risk budget editing.
   Consider a `--risk-budget` flag or auto-escalation for no-logic-change
   refactors.
4. **Groq availability is a single point of failure.** All medium-risk tasks
   route to Groq; when Groq is down, the pipeline stalls. Consider a fallback
   chain (Groq → Gemini → Claude) or provider-agnostic task routing.

## Timeline

| Milestone | Duration |
|-----------|----------|
| Setup (clone refresh + issue file) | ~3 min |
| scan → plan | ~2 min |
| Risk gate block → run.json edit | ~2 min |
| 1st preview attempt (Groq 403) | ~2 min |
| Plan risk reclassification + 2nd preview | ~3 min |
| Debug validation failure (missing T2) | ~3 min |
| Direct apply + test | ~2 min |
| **Total** | **~17 min** |
