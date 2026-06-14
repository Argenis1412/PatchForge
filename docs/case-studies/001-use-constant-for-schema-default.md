# CS-001: Replace hardcoded schema_version default with CURRENT_SCHEMA_VERSION

## Metadata

| Field | Value |
|-------|-------|
| **Issue** | `issues/001-use-constant-for-schema-default.md` |
| **Target repo** | PatchForge (Clon_PatchForge) |
| **Date** | 2026-06-13 |
| **Experiment** | 001 — Clone Workflow POC |
| **Branch** | `feat/experiment-001-dogfooding` |

## Results

| Metric | Value |
|--------|-------|
| Files modified (semantic) | 1 (`artifacts.py`) |
| Infrastructure files | 2 (`executor.py`, `orchestrator.json`) |
| Lines changed | 1 (semantic) + 6 (infrastructure) |
| Tests executed | 288 |
| Tests failed | 0 |
| Ruff | 0 errors |
| Total LLM cost | ~$0.034 (3 plan runs across iterations) |
| Human time | ~20 min (supervision + debugging 3 bugs) |
| Pipeline time (scan → apply) | ~2 min |
| Bugs discovered during experiment | 3 |

## Bugs discovered

| # | Bug | Impact | Fix |
|---|-----|--------|-----|
| 1 | `lineterm=""` in `difflib.unified_diff()` — diff headers concatenated without newlines | Corrupt patch (`git apply` rejects) | Remove `lineterm=""` in `executor.py:414` |
| 2 | LLM returns file without trailing newline — difflib generates no-op hunk | `git apply` rejects as "corrupt patch" | Normalize trailing newline in `executor.py:354-356` |
| 3 | `ruff`/`pytest` not resolved in PATH during post-apply validation | Auto rollback (T-02 worked) | `orchestrator.json` with absolute paths to `.venv` binaries |

## Lessons

1. **The dogfooding pipeline works.** PatchForge successfully planned, executed, validated, and applied a real change to an isolated clone of itself without human intervention in the execution.
2. **Bugs appear where you least expect them.** The diff format (`difflib`) and newline handling were the real problems, not the LLM logic.
3. **T-02 (Atomic Rollback) saved the experiment.** When post-apply validation failed due to PATH, the clone returned to a clean state automatically.
4. **T-01 (Path Traversal Hardening) protected the clone.** The external workspace prevented any leakage to the system.
5. **`orchestrator.json` is required for targets without global tools.** Without absolute paths, `ruff`/`pytest` cannot be found.

## Timeline

| Milestone | Duration |
|-----------|----------|
| Setup (clone + venv + issue file) | ~6 min |
| 1st attempt: scan → plan → preview → apply | ~2 min (broken by bug #1 and #2) |
| Debug + fixes (bug #1 and #2) | ~10 min |
| PATH config (orchestrator.json + bug #3) | ~3 min |
| 2nd attempt (full) | ~2 min — **success** |
| **Total** | **~23 min** |
