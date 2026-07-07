# Experiment: Dogfooding 004 — PatchForge on itself (behavioral change)

**Date:** 2026-07-02
**Target:** `Clon_PatchForge_Proper` — PatchForge codebase at `25d0737`
**Issue:** Extend `_is_dangerous` to detect `requirements*.txt` and `requirements*.in` variants
**Run ID:** `run_20260702_223348_c89b01`
**Provider:** claude-sonnet-4-6 for plan; gemini-2.5-flash via OpenRouter free tier for executor

## Purpose

First experiment targeting a **behavioral logic change** — not metadata or type hints.
Also first experiment using PatchForge as its own target (true dogfooding).
Tests two new dimensions: semantic preservation under logic modification, and architect
file discovery accuracy.

## Locations

```
PatchForge : C:\Users\Visitante\Desktop\Projectos de Github\PatchForge\
Target     : C:\Users\Visitante\Desktop\Projectos de Github\Clon_PatchForge_Proper\
Issue file : docs/experiments/dogfooding-004-issue.md
Workspace  : C:\Users\Visitante\.cache\patchforge\workspaces\1aa57e02233a\
```

## Setup

Clone synced to `main` at `25d0737` (same commit as original). Required
`--validator-timeout 300` because PatchForge's own test suite (619 tests) takes
~106–180s, exceeding the 120s default.

## Pipeline Results

| Step | Result | Detail |
|------|--------|--------|
| `scan` | ✅ | 67 hotspots, V1 supported |
| `plan` | ✅ | 2 tasks, 2 files. $0.02023 (Claude Sonnet 4-6, 910 in / 1167 out) |
| `preview` (first) | ⚠️ | TASK-002 file not found + pytest timeout (120s) |
| `preview` (retry with `--validator-timeout 300`) | ✅ | `status == "previewed"`, `overall_passed == true` |

## Pipeline Metrics

| Metric | Value |
|---|---|
| Total time | ~9 min (scan 10s + plan 45s + preview ~8 min) |
| LLM cost | $0.02023 (plan only; executor used free tier) |
| Final status | `previewed` |
| overall_passed | `true` |
| Files modified | 1 (`src/orchestrator/risk.py`) — TASK-002 silently failed |
| Lines modified | +2 / -0 |

## Product Metrics

| Metric | Value |
|---|---|
| Did the patch resolve exactly the issue? | YES (code logic) |
| Were there changes outside scope? | NO |
| Was it applied without human edits? | YES (for the generated code) |
| Would the diff be accepted in a real PR? | PARTIALLY — tests missing |

## Human Interventions

| Type | Count | Detail |
|---|---|---|
| Formatting | 0 | — |
| Logic correction | 0 | — |
| Workaround (timeout) | 1 | `--validator-timeout 300` required |
| Missing deliverable (tests) | — | TASK-002 never applied; no new tests |
| **Total edits to generated code** | **0** | |

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

**Semantic correctness:** The executor correctly replicated the existing pattern used
for docker-compose variants. All 8 ACs from the issue are satisfied by the generated code.

## Behavior Confidence Assessment

| Level | Criterion |
|---|---|
| **Partial** | Existing `test_exact_basename_match` covers `requirements.txt` (regression OK) |
| **Missing** | No tests for `requirements-dev.txt`, `requirements.in`, etc. |

The executor understood the semantic intent and wrote correct code, but couldn't add
tests because the architect hallucinated the test file name.

## Failures and Root Causes

### Failure 1: Architect filename hallucination

The architect planned TASK-002 targeting `tests/test_risk.py`, which does not exist.
The actual test file is `tests/test_risk_gate.py`. The executor reported "File not found"
and silently skipped TASK-002. The rest of the pipeline continued unaffected.

**Root cause:** The architect (Claude Sonnet 4-6) was given the scan hotspots but not
the explicit list of test files. It inferred the test file name from the module name
(`risk.py` → `test_risk.py`) without verifying it exists.

**Impact:** No tests were added for the new behavior. The pipeline still passed because
the existing tests cover the regression case (`requirements.txt`), but the new variants
are untested.

### Failure 2: Validator timeout at 120s default

PatchForge's own test suite (619 tests) runs in ~106–180s. The default validator
timeout is 120s, which is too tight for this target. First preview attempt timed out.

**Workaround:** `--validator-timeout 300` resolves this.

**Impact:** False FAILED result on first run; requires human knowledge of the timeout
flag. Not a pipeline bug — a configuration mismatch between the default and this target.

## Comparison with Dogfooding 003

| Metric | Dogfooding 003 | Dogfooding 004 |
|---|---|---|
| Change type | Metadata (Field()) | Logic (condition added) |
| Executor semantic reasoning required | Low | Medium |
| overall_passed | ✅ | ✅ (after timeout fix) |
| Core code correct | ✅ | ✅ |
| Tests added | N/A | ❌ (architect hallucination) |
| Human edits to code | 0 | 0 |
| Workarounds | 0 | 1 (timeout flag) |

## Verdict

```
Issue:
Extend _is_dangerous to detect requirements file variants

Core change (TASK-001):
PASS — executor correctly reasoned about the existing pattern (docker-compose variants)
and applied the same logic to requirements files. Zero human edits to the code.

Test delivery (TASK-002):
FAIL — architect hallucinated test file name; no new tests generated

Pipeline reliability:
PARTIAL PASS — with --validator-timeout 300; first run fails silently on TASK-002

Would I merge this PR exactly as generated?
NO — tests for the new behavior are missing. Code is correct but not verified.
Would merge after manually adding ~5 lines to test_risk_gate.py.
```

## Findings (for discovery log)

| ID | Severity | Description | File |
|---|---|---|---|
| D-001 | Medium | Architect hallucinates test file names when test directory is not explicitly scanned | `src/orchestrator/agents/architect/` |
| D-002 | Low | Default validator timeout (120s) is too short for large test suites (PatchForge itself) | `src/orchestrator/agents/validator/runners.py` |
| D-003 | Low | TASK failure ("File not found") is logged but does not block `overall_passed=true`; can mask missing deliverables | `src/orchestrator/commands/preview.py` |

## Lessons

1. The executor can correctly reason about existing code patterns and extend them — TASK-001 is semantically correct.
2. Architect file discovery is the weak point: without explicit test file enumeration, hallucinated names silently produce incomplete patches.
3. PatchForge scanning itself reveals its own configuration limits (120s timeout).
4. `overall_passed=true` can be misleading when a TASK silently fails — the QA passed because existing tests covered the regression, not the new behavior.

## Recommendation

Two follow-up actions from this experiment:

1. **D-001**: Give the architect access to the test file list during planning (or scan `tests/` explicitly). This is a planning improvement, not a pipeline bug.
2. **D-002/D-003**: Either raise the default timeout to 300s or add a warning when a TASK fails and `overall_passed=true`, so the user knows the deliverable is incomplete.

Dogfooding 005 should test whether D-001 can be reproduced with a different module to
confirm the pattern, or try a multi-file change to stress the scope-control invariant.
