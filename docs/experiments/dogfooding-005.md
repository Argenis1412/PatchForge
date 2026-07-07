# Experiment: Dogfooding 005 — E2E verification of D-001/D-002/D-003 fixes

**Date:** 2026-07-07
**Target:** `Clon_PatchForge_Proper` — PatchForge codebase at `cd6b689` (post-PR-#195)
**Issue:** Extend `_is_dangerous` to detect `requirements*.txt` and `requirements*.in` variants (same as dogfooding-004)
**Run ID:** `run_20260707_154333_a881ef`
**Provider:** claude-sonnet-4-6 for plan; gemini-2.5-flash via OpenRouter free tier for executor

## Purpose

Verify that the three silent-failure fixes from PR #195 (Issue #194) work correctly
in a live run. Dogfooding-004 revealed D-001, D-002, and D-003. This run re-executes
the same scenario with the patched PatchForge to observe whether each fix fires or not.

No code changes were made during this experiment.

## Locations

```text
PatchForge : C:\Users\Visitante\Desktop\Projectos de Github\PatchForge\
Target     : C:\Users\Visitante\Desktop\Projectos de Github\Clon_PatchForge_Proper\
Issue file : docs/experiments/dogfooding-004-issue.md
Workspace  : C:\Users\Visitante\.cache\patchforge\workspaces\1aa57e02233a\
```

## Setup

Clone synced to `cd6b689` (merge commit of PR #195 into clone's main). Ran `uv sync`
in the clone to restore the `.venv` (absent after git pull, gitignored). PatchForge
venv activated before scan to ensure `ruff`/`pytest` are discoverable via
`shutil.which()`. Risk budget set to `medium` (same as dogfooding-004).

## Pipeline Results

| Step | Result | Detail |
|------|--------|--------|
| `scan` | ✅ | 69 hotspots (vs 67 in D004), V1 supported, risk_budget=medium |
| `plan` | ✅ | 2 tasks, 2 files. Architect hallucinates `tests/test_risk.py` again. D-001 does NOT intercept (see checkpoint below). $0.01948 (Claude Sonnet 4-6, 910 in / 1117 out) |
| `preview` | ✅ NEW BEHAVIOR | TASK-002: File not found. D-003 fix fires: `executor_had_errors=true`, `status=validation_failed`, panel "✘ Preview completed with failures". pytest timeout at 300s (new finding D-004). |
| `apply` | ✅ BLOCKED | "Patch validation failed during preview" — correctly rejected. |

## Checkpoint Analysis: D-001 / D-002 / D-003

### D-001 — Phantom path validation

**Status: NOT exercised** (known limitation, expected behavior)

The architect planned TASK-002 targeting `tests/test_risk.py` (still hallucinating the
same wrong filename). However, `validate_plan_paths()` evaluates:

- `tests/test_risk.py` does not exist → `abs_path.exists() == False`
- `tests/` directory exists → `abs_path.parent.exists() == True`
- → classified as **new-file creation intent** → not rejected

The D-001 fix only blocks phantom paths where the parent directory also does not
exist. This is the documented partial-fix boundary: architect hallucinations within
existing directories are not detected. The fix works as designed; this scenario is
outside its stated recall.

**Root cause still open:** architect lacks explicit file listing. Deferred to P4
("context-scoping para arquitecto").

### D-002 — Default timeout raised to 300s

**Status: PARTIAL** — fix verified, but 300s still insufficient for this target

The timeout message confirms the new default is in effect:
```text
Timeout: pytest exceeded 300s limit. Increase with --validator-timeout <seconds>.
```

In dogfooding-004, the suite (619 tests) timed out at 120s and passed with
`--validator-timeout 300`. Now (633 tests, post-PR-#195), the suite exceeds 300s.
Ruff completed without timeout. Only pytest was affected.

The fix raises the bar correctly, but the PatchForge test suite has grown beyond the
new default. See D-004 below.

### D-003 — Executor hard errors surface as `validation_failed`

**Status: FULLY VERIFIED ✅**

`run.json` after preview:
```json
{
  "status": "validation_failed",
  "executor_had_errors": true,
  "validation_summary": "Incomplete deliverables: 1 task(s) failed (TASK-002). Timeout: pytest exceeded 300s limit. ..."
}
```

Panel output:
```text
✘ Preview completed with failures
Validation Status: FAILED
```

In dogfooding-004, the same scenario produced `status: "previewed"` and a green panel
despite TASK-002 failing silently. The fix correctly surfaces the failure and blocks
apply. The priority order in `validation_summary` is also correct: "Incomplete
deliverables" precedes the timeout message.

## Pipeline Metrics

| Metric | Value |
|---|---|
| Total time | ~6m 40s (scan ~1s + plan ~90s + preview ~5m 9s) |
| LLM cost | $0.01948 (plan only; executor used free tier) |
| Final status | `validation_failed` |
| `executor_had_errors` | `true` |
| `overall_passed` (validator) | `false` (pytest timeout) |
| Files modified by executor | 1 (`src/orchestrator/risk.py` — TASK-001 only) |
| Lines modified | +2 / -0 |

## Generated Patch

```diff
--- a/src/orchestrator/risk.py
+++ b/src/orchestrator/risk.py
@@ -49,6 +49,8 @@
         return True
     if name.startswith("docker-compose.") and (name.endswith(".yml") or name.endswith(".yaml")):
         return True
+    if name.startswith("requirements") and (name.endswith(".txt") or name.endswith(".in")):
+        return True
     for parent in p.parents:
         candidate = str(parent).replace("\\", "/") + "/"
         if candidate in DANGEROUS_PATTERNS:
```

Identical to dogfooding-004. Semantically correct. Ruff: ✅ (0 errors).

## Comparison with Dogfooding 004

| Metric | Dogfooding 004 | Dogfooding 005 |
|---|---|---|
| Target commit | `25d0737` | `cd6b689` (post-PR-#195) |
| Hotspots | 67 | 69 (+2 new files from PR #195) |
| Architect hallucination (test file) | `tests/test_risk.py` | `tests/test_risk.py` (identical) |
| D-001 interception | N/A (fix not yet deployed) | Not intercepted (known limitation) |
| Timeout default | 120s | 300s |
| Timeout outcome | Timeout at 120s | Timeout at 300s (suite grew) |
| `status` after preview | `previewed` (BUG) | `validation_failed` (FIXED ✅) |
| `executor_had_errors` | absent (field not exist) | `true` (field present ✅) |
| Panel | ✔ green (BUG) | ✘ red (FIXED ✅) |
| Apply | Possible (BUG) | Blocked (FIXED ✅) |

## New Finding: D-004

| ID | Severity | Description |
|---|---|---|
| D-004 | Low | 300s validator timeout still insufficient for self-dogfooding PatchForge post-PR-#195 |

The PatchForge test suite grew from 619 to 633 tests after PR #195 (added
`test_plan_validation.py`, `test_preview_hard_errors.py`, and extended
`test_validator_timeout.py`). Combined with existing suite runtime, the suite now
exceeds 300s when run via PatchForge's own validator. The `--validator-timeout`
hint is correctly displayed.

Workaround: `--validator-timeout 450` or `--validator-timeout 600`.

D-002 fix is directionally correct but the default needs another raise for
self-dogfooding to work without flags. Alternatively, the validator could
auto-detect suite size.

## Verdict

```text
Issue:
Extend _is_dangerous to detect requirements file variants

D-001 (phantom path rejection):
NOT EXERCISED — architect hallucination within existing directory is the
documented partial-fix boundary. Fix works for true phantom paths (parent
dir also missing). Root cause (architect lacking file listing) deferred to P4.

D-002 (default timeout):
PARTIAL — timeout raised to 300s (verified). Still insufficient for the
633-test suite produced by PR #195 itself. New finding D-004 documents this.
Hint message is correct and actionable.

D-003 (executor errors surface as validation_failed):
PASS ✅ — executor_had_errors=true, status=validation_failed, panel shows
failures, apply is blocked. Complete behavioral fix confirmed.

Pipeline reliability (overall):
PARTIAL PASS — D-003 fix works. D-002 requires --validator-timeout for
self-dogfooding. D-001 did not trigger (outside its recall boundary).

Would I merge the generated patch as-is?
NO — same reason as dogfooding-004: tests for new behavior are missing
(TASK-002 never ran). Code is correct; needs ~5 manual lines in
tests/test_risk_gate.py.
```

## Lessons

1. **D-003 fix is production-ready.** The executor error surfacing works exactly as
   designed: `executor_had_errors=true`, `validation_failed` status, red panel, apply
   blocked. The priority ordering in the summary (incomplete deliverables before timeout)
   is correct and useful.

2. **D-001 has structural recall limitations.** The architect repeats the same hallucination
   (`test_risk.py` → `test_risk_gate.py`) across two independent runs with the same
   model. The fix correctly rejects paths in non-existent parent directories but cannot
   detect plausible hallucinations within existing directories. Context-scoping (P4) is
   the real fix.

3. **The hardening PR made itself harder to self-dogfood.** PR #195 added 14 new tests.
   Those tests pushed the suite beyond 300s. D-002 fix raised the floor but the floor
   moved up simultaneously. This is not a regression in the fix — it is a sign that
   PatchForge's own test suite is growing and the self-dogfooding scenario requires
   explicit timeout configuration.

4. **Scan hotspot count reflects codebase state.** 69 vs 67 hotspots: the two new files
   (`plan_validation.py`, `test_plan_validation.py`) added by PR #195 are now in scope.

## Follow-up Actions

| ID | Priority | Action |
|---|---|---|
| D-004 | Low | Raise `DEFAULT_TIMEOUT` to 450s or add a `--validator-timeout` preset for self-dogfooding in docs |
| D-001 | Deferred P4 | Give architect access to file listing during planning |
| — | Optional | Dogfooding-006: test a multi-file behavioral change to stress scope-control invariant |
