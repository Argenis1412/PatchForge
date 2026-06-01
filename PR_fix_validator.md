## fix: Validator validates staged changes instead of original tree

Closes #14

### Problem

The Validator agent runs `ruff check .` and `pytest .` against `config.target_path.resolve()` (the original repository). When the Executor writes LOW/MEDIUM changes to `outputs/staging/<run_id>/`, those changes are completely invisible to validation.

### Root Cause Chain

- `pipeline.py:267`: `_stage_validator()` calls `run_validator(config=self.config)` with no staging parameter.
- `validator.py:245`: `run()` hardcodes `project_root = config.target_path.resolve()`.
- `validator.py:136-148`: `run_ruff()` and `run_pytest()` execute against `project_root`.

### Solution

1. Added `staging_dir: Path | None = None` parameter to `validator.run()` and all tool runners.
2. **`run_ruff`:** When `staging_dir` is provided, targets explicit staged file paths (`ruff check <path1> <path2> ...`) instead of `.`.
3. **`run_pytest` / `run_tsc`:** When `staging_dir` is provided, creates a temporary overlay combining original and staged files, runs the tool against this overlay, then discards it.
4. **`pipeline._stage_validator()`:** Now passes `staging_dir` obtained from `self.workspace.staging_dir_for_run(self.run.run_id)`.

### New helpers

- `_collect_staged_files(staging_dir)` — returns sorted list of regular files in the staging directory.
- `_create_overlay(project_root, staging_dir, ignore_dirs)` — creates a temp directory mirroring the project with staged files overlaid on top.

### Verification

- ✅ Ruff lint — 0 new errors (only pre-existing)
- ✅ All 43 existing tests pass

### Acceptance Criteria

| Criterion | How it's met |
|---|---|
| Lint errors → validation fails | Ruff checks staged files explicitly |
| Test breaking → validation fails | Pytest runs against overlay with changes applied |
| No changes → passes against original tree | `staging_dir` empty/falsy → fallback to original behavior |
