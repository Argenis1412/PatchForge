---
title: Move write_verdict() from schemas/experiment.py to workspace.py
severity: low
labels: refactor
---
## Problem

`write_verdict()` in `src/orchestrator/schemas/experiment.py` co-locates file I/O
with schema definition. The codebase pattern puts all I/O in `workspace.py` via
`WorkspaceManager` methods (`write_artifact`, `write_run_json`, etc.). This
inconsistency is documented as technical debt in `discoveries.md` (Issue #79).

## Required Change

### 1. `src/orchestrator/workspace.py`

Add `write_verdict(run_id: str, verdict: Verdict) -> None` as a method on
`WorkspaceManager`, consistent with the existing `write_run_json()` pattern.
The method writes `verdict.json` (JSON serialization) and `verdict.md`
(markdown summary) to the run directory.

Add `_write_verdict_markdown(path: Path, verdict: Verdict) -> None` as a
module-level private helper.

### 2. `src/orchestrator/schemas/experiment.py`

Remove `write_verdict()` and `_write_verdict_markdown()` functions. Leave
`Verdict(BaseModel)` — the pure schema — in place.

### 3. `tests/test_experiment_schema.py`

Update imports: import `WorkspaceManager` from `orchestrator.workspace` and
use `WorkspaceManager(tmp_path).write_verdict("run_001", v)` instead of the
standalone `write_verdict(tmp_path, v)` call. The `test_write_verdict_file_not_found_error`
test should verify behavior via `WorkspaceManager`.

## Scope

- Exactly 3 files modified: `workspace.py`, `schemas/experiment.py`, `test_experiment_schema.py`
- No pipeline logic touched
- `Verdict` schema stays in `schemas/experiment.py`
- No new dependencies introduced

## Acceptance Criteria

- `WorkspaceManager.write_verdict(run_id, verdict)` writes `verdict.json` and
  `verdict.md` to the run directory
- `schemas/experiment.py` contains only `Verdict(BaseModel)` — no I/O functions remain
- `ruff check .` — 0 errors
- `pytest` — all existing tests pass without modification
- Debt entry #79 in `discoveries.md` is updated to resolved state
