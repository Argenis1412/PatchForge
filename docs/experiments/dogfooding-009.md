# Experiment: Dogfooding 009 — Validator PATH resolution via `sys.executable`

**Date:** 2026-07-12
**Target:** PatchForge codebase at `5525784` (main, post-P3 roadmap consolidation)
**Issue:** Validator subprocess runners (`run_ruff`, `run_pytest`) fail with "Command not found" when no `.venv` exists in the target and ruff/pytest are not on the system PATH — common on Windows.
**Fix under test:** Default commands changed from bare `["ruff", "check"]` / `["pytest", "..."]` to `[sys.executable, "-m", "ruff", "check"]` / `[sys.executable, "-m", "pytest", "..."]`, ensuring the same Python interpreter that runs PatchForge is used to discover tools.
**Run ID:** `run_20260712_053127_3d5b7c`
**Provider:** claude-sonnet-4-6 for plan
**Budget:** ~$0.02 total (plan only — executor returned NOOP)

## Locations

```text
PatchForge : C:\Users\Visitante\Desktop\Projectos de Github\PatchForge\
Target     : %TEMP%\patchforge-dogfooding-009\target\
Issue file : target\dogfooding-009-issue.md
Workspace  : %TEMP%\patchforge-dogfooding-009\workspace\
```

## Setup

Fresh clone from local PatchForge repository to `%TEMP%\patchforge-dogfooding-009\target`. No `.venv` was set up in the clone — this is the nominal condition that reproduces the PATH resolution failure. The `.env` file with API keys was copied for provider access.

The issue file asked to add a module-level docstring to `src/orchestrator/paths.py` — a file that already has a detailed docstring with monkeypatch instructions. This was intentional: to verify the LLM correctly identifies NOOP conditions rather than blindly replacing content.

## Pipeline Results

| Step | Result | Detail |
|------|--------|--------|
| `scan --risk-budget medium` | ✅ | 72 hotspots found; run created |
| `plan --issue-file issue.md` | ✅ | 1 task planned (T-001: add docstring to `paths.py`); model claude-sonnet-4-6; cost $0.022 |
| `preview` | ✅ NOOP | Executor returned no changes — LLM identified that `paths.py` already has a docstring and correctly produced an empty patch |

## Analysis

### Validator PATH fix

The previous attempt (same dogfooding run without the fix) failed at `preview` because `ruff` and `pytest` subprocesses returned "Command not found" — the clone had no `.venv`, and the system `PATH` on Windows does not include Python's `Scripts` directory by default.

After changing the default commands in `runners.py` from bare tool names to `[sys.executable, "-m", "tool_name"]`:
- **ruff**: `python -m ruff check .` → resolves via the same Python interpreter that runs PatchForge
- **pytest**: `python -m pytest . --tb=short -q` → same guarantee
- Tools are always found because ruff and pytest are installed as project dependencies
- `cmd_override` via `orchestrator.json` still works (bypasses the default)
- Existing venv detection (`_build_env_with_venv`) still works for targets with a local `.venv`

### LLM behavior

The LLM correctly handled the NOOP case:
- Inspected `paths.py` and found the existing docstring
- Determined no change was needed
- Returned empty patch rather than replacing or duplicating content

This is an improvement over dogfooding-008 where the LLM generated a regressive patch (replacing the detailed docstring with a trivial one).

## Verdict

**PASS.** The `sys.executable -m` fix resolves the validator PATH resolution failure on Windows targets without a `.venv`. The pipeline completed end-to-end with no errors. No regressions observed in the executor, planner, or validator stages.

The change has been committed and verified by the full QA suite (714 passed, 2 skipped; `ruff check` 0 errors).
