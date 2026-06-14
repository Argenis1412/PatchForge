# CS-004: Executor DAG Scheduler — task dependency resolution

## Metadata

| Field | Value |
|-------|-------|
| **Issue** | #98 |
| **Target repo** | PatchForge (direct) |
| **Date** | 2026-06-14 |
| **Experiment** | 004 — DAG scheduler |
| **Branch** | `feat/issue-98-executor-dag-scheduler` |

## Problem

The executor iterated `implementation_plan` in declaration order and applied
every task unconditionally, ignoring `Task.dependencies`. When a dependency
task produced no changes (idempotent), the downstream task still executed and
could overwrite staging files with an incorrect or empty result, producing an
incomplete patch that failed validation. This was Bug #1 discovered during
Experiment 002 dogfooding.

## The fix

Replaced the flat sequential loop with a DAG-aware scheduler:

1. **`TaskStatus(str, Enum)`** — 5 members: `APPLIED`, `NOOP`, `SKIPPED`,
   `ERROR`, `PENDING_REVIEW`. Each `FileChange` carries a typed status.
2. **`_build_dag()`** — Validates every `task.dependencies` reference exists
   in the plan; raises `SchedulerInvariantError` otherwise.
3. **`_topological_order()`** — Kahn's algorithm, O(V²) deterministic scan
   in declaration order. Raises `CycleDetectedError` on cycle.
4. **Scheduler loop** — For each task in topological order:
   - Checks `dependency_satisfied()` — blocking statuses: `ERROR`,
     `SKIPPED`, `PENDING_REVIEW`
   - Routes per-file changes to `applied`, `pending_review`, or `errors`
   - Aggregates worst status across files for dependency tracking
   - NOOP tasks get `status=NOOP, diff=None` (filtered by `preview.py`
     `if change.diff` guard — unchanged)

## Key design decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **DAG algorithm** | Kahn's BFS, O(V²) scan | Deterministic ordering; no heap needed for V1 (<50 tasks) |
| **Status aggregation** | Worst wins (ERROR > PENDING_REVIEW > APPLIED > NOOP) | Multi-file tasks with mixed statuses must not report false success |
| **PENDING_REVIEW blocking** | Blocks downstream | HIGH-risk tasks never write to staging; downstream would operate on stale state |
| **SKIPPED routing** | `ExecutorOutput.errors` | Same list as ERROR; differentiated by `status` field |
| **Guard failures** | `SchedulerInvariantError` (typed) | Clear error, not cryptic `KeyError` |

## Results

| Metric | Value |
|--------|-------|
| Files modified | 4 (`exceptions.py`, `executor_output.py`, `executor.py`, `discoveries.md`) |
| Files added | 1 (`test_executor_scheduler.py`) |
| Lines added | ~460 |
| Lines removed | ~20 |
| Tests added | 16 (11 scenarios + 5 building blocks) |
| Tests executed | 310 (292 old + 16 new + 2 skipped) |
| Tests passed | 308 |
| Ruff | 0 errors |
| Format | clean |

## Files changed

| File | Action |
|------|--------|
| `src/orchestrator/exceptions.py` | EDIT — `CycleDetectedError`, `SchedulerInvariantError` |
| `src/orchestrator/schemas/executor_output.py` | EDIT — `TaskStatus` enum, `FileChange.status` type |
| `src/orchestrator/agents/executor.py` | EDIT — DAG functions, scheduler loop, NOOP routing |
| `tests/test_executor_scheduler.py` | ADD — 16 tests |
| `docs/context/discoveries.md` | EDIT — mark Experiment 002 debt resolved |

## Files explicitly untouched

- `preview.py`
- `plan.py`
- `apply.py`
- `pipeline.py`
- `pipeline_run.py`
- `architect_output.py` (Task schema already had `dependencies` field)

## Lessons

1. **The `dependencies` field existed but was dead code** — `Task.dependencies:
   List[str] = Field(default=[])` was already in `architect_output.py` from
   the initial schema design. Only the consumer needed to change.
2. **Multi-file task aggregation is subtle** — A task modifying 3 files where
   the first 2 succeed and the 3rd fails must be reported as `ERROR` for
   dependency tracking. Per-file output in `ExecutorOutput` is fine; the
   aggregation map (`task_status_results`) is separate from the output lists.
3. **PENDING_REVIEW is not a failure but must block downstream** — Since
   HIGH-risk tasks never write to staging (`task.risk_level == "high"` returns
   `FileChange(status=TaskStatus.PENDING_REVIEW)` without calling
   `staging_path.write_text()`), any downstream task depending on it would
   operate on stale pre-A state. Conservador, fail-safe.
4. **The `if change.diff` guard in preview.py already handles NOOP** — The
   existing `preview.py` code filters changes by `if change.diff:` before
   appending to the consolidated patch. Returning `diff=None` for NOOP tasks
   means zero changes needed outside the executor.
