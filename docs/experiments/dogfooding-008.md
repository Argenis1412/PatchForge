# Experiment: Dogfooding 008 — Redundant `orchestrator.json` reads in `doctor.py`

**Date:** 2026-07-10
**Target:** `Clon_PatchForge_Proper` — PatchForge codebase at `fa4219e` (synced to main)
**Issue:** `doctor.py:check()` reads `orchestrator.json` 3x per invocation via
`check_workspace()`, `check_ruff()`, `check_pytest()`
**Run ID:** `run_20260710_211931_8032ea`
**Provider:** claude-sonnet-4-6 for plan (forced — see Deviations); Gemini 2.5 Flash for
executor (forced via `--force-provider gemini`)
**Budget:** ~$1.30 OpenRouter credit available; actual spend ~$0.10 (plan retries, Claude
via `ANTHROPIC_API_KEY`, not OpenRouter)

## Locations

```text
PatchForge : C:\Users\Visitante\Desktop\Projectos de Github\PatchForge\
Target     : C:\Users\Visitante\Desktop\Projectos de Github\Clon_PatchForge_Proper\
Issue file : docs/experiments/dogfooding-008-issue.md
Workspace  : C:\Users\Visitante\.cache\patchforge\workspaces\1aa57e02233a\
```

## Setup

Clone was stale (8 commits behind main, plus 4 local merge commits from prior dogfooding
runs never present upstream). Backed up the divergent local history to branch
`backup/pre-dogfooding-008-*` and hard-reset the clone to main's `fa4219e`. The clone's
untracked `orchestrator.json` (points `test_command`/`lint_command` at PatchForge's own
`.venv`) was preserved as-is — it's local, per-clone setup, not repo state.

## Deviations from the prescribed pipeline

The task instructions called for `patchforge plan --issue-file issue.md --force-provider
gemini <target>`, but the actual CLI (`plan --help`) does not expose `--force-provider` —
only `preview` and `ci` do. `plan` always uses its configured Architect model
(`claude-sonnet-4-6` via `ANTHROPIC_API_KEY`), regardless of provider flags. This is worth
fixing in either the CLI (add `--force-provider` to `plan`) or the task runbook (stop
claiming `plan` supports it). Filed as a discovery below rather than fixed here (out of
scope for this dogfooding run).

Additionally, `plan` requires an explicit `--workspace` pointing at the same workspace
`scan` used — omitting it makes `plan` look for the run in a *different* auto-computed
workspace hash and fail with "Run ... does not exist". Not documented in `--help`.

## Pipeline Results

| Step | Result | Detail |
|------|--------|--------|
| `doctor` | ⚠️ | V1 supported, dirty tree (expected — untracked issue.md), no API keys in shell env (used `--env-file .env`) |
| `scan` (medium risk budget) | ⚠️ exit 1 | 72 hotspots found; run created successfully. "V1 supported: no — Ruff/Pytest not found in PATH" (PATH check, not `orchestrator.json`-aware; harmless for this run) |
| `plan` (1st attempt, risk_budget=low default) | ❌ blocked | Risk gate: both tasks are medium-risk, budget was low. Required re-scanning with `--risk-budget medium` |
| `plan` (2nd attempt, risk_budget=medium) | ❌ blocked | "invalid file references: Task(s) with empty files_to_modify: T1" — non-deterministic Architect output |
| `plan` (3rd attempt, same inputs) | ✅ | 2 tasks planned: T1 (`doctor.py` refactor), T2 (`test_doctor.py` update). Cost $0.0335, 5271 in / 1181 out tokens |
| `preview --force-provider gemini` | ⚠️ VALIDATION_FAILED | T1 patch generated. T2 failed: Gemini free-tier quota exhausted (429, `generate_content_free_tier_requests` limit 20/day) |

## Patch Quality Analysis

The Architect's plan (T1) was well-scoped and correctly diagnosed the fix: read
`_read_orchestrator_config()` once in `check()`, pass the result as an optional `config`
parameter to `check_workspace()`, `check_ruff()`, `check_pytest()`, with each sub-check
falling back to its own read when called standalone (preserving backward compatibility for
external callers). This matches the intended fix almost exactly.

The generated diff (`patch.diff`, 87 lines) implemented that shape correctly, **but
introduced one real bug**:

```python
orchestrator_config = _read_orchestrator_config(target)
if not orchestrator_config:
    checks.append(CheckResult(
        name="orchestrator_json",
        status=CheckStatus.FAIL,
        message="orchestrator.json not found or malformed",
        ...
    ))
```

This unconditionally appends a hard-`FAIL` check whenever `orchestrator.json` is absent —
but `orchestrator.json` is optional by design (`check_ruff`/`check_pytest` already fall back
to PATH-detecting `ruff`/`pytest` when it's missing, with no failure). The new check broke
`v1_supported` for every repo without an `orchestrator.json`, failing 9 pre-existing tests
in `test_doctor.py` / `test_baseline_cli.py`. Validation (`ruff` + `pytest`) correctly caught
this — the pipeline did its job by blocking on `VALIDATION_FAILED` rather than silently
producing a regression.

A secondary, trivial issue: one line (the `check_workspace` signature) exceeded the 100-char
ruff limit (109 chars) — cosmetic, would have been fixed by `ruff format`.

## Manual Fix (per "one pipeline run max" budget rule)

T2 never got a chance to run (Gemini quota exhausted), so its test-suite update also never
materialized. Applied both fixes by hand in the clone:

1. `check_workspace`, `check_ruff`, `check_pytest` — added optional `config: Optional[dict]`
   param, falling back to `_read_orchestrator_config(path)` when not supplied (exact shape
   the Architect planned and the executor implemented correctly).
2. `check()` — reads `orchestrator.json` once via `_read_orchestrator_config(target)` and
   passes it to the three sub-checks. **Did not** add the spurious `orchestrator_json` FAIL
   check — that behavior was never part of the issue's desired outcome and duplicates
   existing graceful-fallback logic.
3. Added `TestCheck.test_reads_orchestrator_config_once` — wraps
   `_read_orchestrator_config` with a mock and asserts `call_count == 1` after `check()`.
4. `ruff format` reformatted `doctor.py` (line-length fix, same one flagged by validation).

## QA (in the clone)

```
ruff check .            → All checks passed!
ruff format --check .   → 127 files already formatted (after ruff format run)
pytest tests/ -q        → 1 failed, 709 passed, 2 skipped
```

The one failure, `test_executor_emits_task_skipped`, is unrelated to this change (lives in
`test_executor.py`, touches circuit-breaker skip events) and passes in isolation
(`pytest tests/test_executor.py::test_executor_emits_task_skipped` → 1 passed). Reproduced
twice in the full-suite run — order-dependent flakiness from shared circuit-breaker module
state leaking across tests, pre-existing and out of scope. Logged as a discovery below.

## Discoveries

### D-008a — `plan` does not accept `--force-provider`

- **File:** `src/orchestrator/cli.py` (or wherever the `plan` Typer command is defined)
- **Behaviour:** `preview` and `ci` both expose `--force-provider ('gemini'|'openrouter'|
  'claude')`, but `plan` does not — the Architect step always uses its configured default
  (`claude-sonnet-4-6` via `ANTHROPIC_API_KEY`), ignoring provider-forcing intent.
- **Impact:** Cost-budget-constrained dogfooding runs (like this one) cannot force the
  planning step onto a specific/free provider; only `preview`'s executor and validator
  steps can be redirected. `plan` cost $0.03/run here regardless.
- **Why deferred:** Out of scope for this refactor; either add `--force-provider` to
  `plan` or update the dogfooding runbook to stop implying it works there.

### D-008b — `plan` requires `--workspace` to match `scan`'s, or fails to find the run

- **File:** `src/orchestrator/cli.py` (`plan` command), workspace hash resolution
- **Behaviour:** `scan <path>` computes a workspace path from a hash of the target path and
  reports it in its output. `plan <run_id>` without an explicit `--workspace` computes a
  *different* hash and fails with "Run ... does not exist in workspace ...".
- **Impact:** Every fresh pipeline run needs the operator to manually copy the workspace
  path from `scan`'s output into `plan --workspace ...` (and `preview --workspace ...`).
  Undocumented in `--help`.
- **Why deferred:** Likely intentional (workspace is derived once and meant to be passed
  explicitly), but the failure mode is confusing and worth a `--help` note or doctor hint.

### D-008c — Architect plan generation is non-deterministic on file references

- **File:** Architect prompt/output validation (`plan` command)
- **Behaviour:** Running `plan` twice with identical inputs (same issue file, same risk
  budget) produced different outcomes: attempt 2 failed with "Task(s) with empty
  files_to_modify: T1", attempt 3 (same command) succeeded with a correct 2-task plan.
- **Impact:** Pipeline runs are not fully reproducible; operators must be prepared to retry
  `plan` on validation failures unrelated to the issue content.
- **Why deferred:** Inherent to LLM sampling variance; a stricter output schema or
  lower-temperature Architect call could reduce this, but out of scope here.

### D-008d — `check()` FAIL-on-missing-`orchestrator.json` regression (caught, not landed)

- **File:** `src/orchestrator/doctor.py:check()`
- **Debt:** The executor's generated patch for this issue added an unconditional
  `orchestrator_json` FAIL check whenever `_read_orchestrator_config()` returns `{}`,
  breaking `v1_supported` for the common case of a repo with no `orchestrator.json` (9 test
  failures). Caught by `preview`'s validation gate and never applied — documented here only
  because it's a useful illustration of "single read, reuse across sub-checks" being
  interpreted by the LLM as "also assert the file exists," which the issue never asked for.
  Fixed manually without that extra check (see Manual Fix above).
- **Discovered by:** Dogfooding 008 preview validation
- **Why deferred:** Not landed; the manual fix superseded it. No further action needed.

### D-008e — `test_executor_emits_task_skipped` is order-dependent

- **File:** `tests/test_executor.py`
- **Behaviour:** Fails when run as part of the full `pytest tests/ -q` suite (`assert 0 ==
  1`, expects one `task_skipped` event, gets zero) but passes reliably in isolation. Almost
  certainly shared module-level circuit-breaker state (`_cb_gemini` or similar) leaking
  between tests that don't fully reset it.
- **Discovered by:** Dogfooding 008 full-suite QA run (reproduced twice)
- **Why deferred:** Unrelated to the `doctor.py` config-read fix; pre-existing flakiness.
  Needs its own investigation into test isolation for circuit-breaker globals.

## Verdict

Pipeline ran end-to-end within the one-run budget: `scan` → `plan` (3 attempts, 2 blocked
by risk gate / non-determinism) → `preview` (blocked by Gemini free-tier quota on T2, but
T1's diff was generated and validated). The core refactor (single read, reused config) was
correctly scoped and mostly correctly implemented by the LLM; validation caught a real
regression before it could land. Fixed manually per the budget constraint. `ruff check`,
`ruff format --check`, and `pytest` all pass except one pre-existing, unrelated flaky test.
