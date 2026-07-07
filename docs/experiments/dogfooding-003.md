# Experiment: Dogfooding 003 — Portfolio Backend (CRLF fix validation)

**Date:** 2026-07-02
**Target:** `Portf-lio/backend/` — FastAPI + Pydantic V2
**Issue:** Add Field() metadata to `PhilosophyItemSchema` bare fields (`id`, `name`, `image_url`)
**Run ID:** `run_20260702_220430_798b49`
**Provider:** claude (claude-sonnet-4-6) for plan; gemini-2.5-flash via OpenRouter free tier for executor

## Purpose

Validate that the CRLF fix (Issue #192, PR #193) lets PatchForge complete a fully automatic
cycle on Windows — the failure mode discovered in Dogfooding 002.

## Locations

```
PatchForge : C:\Users\Visitante\Desktop\Projectos de Github\PatchForge\
Target     : C:\Users\Visitante\Desktop\Projectos de Github\Portf-lio\backend\
Issue file : docs/experiments/dogfooding-002-issue.md
Workspace  : C:\Users\Visitante\.cache\patchforge\workspaces\d3420453496d\
```

## Setup

The target file was reverted to its pre-Dogfooding-002 state (bare fields without `Field()`)
to reproduce the exact same scenario. Target repo `core.autocrlf=true` (Windows default).
Scan required `--risk-budget medium` and target venv injected into PATH (same as Dogfooding 002).

## Pipeline Results

| Step | Result | Detail |
|------|--------|--------|
| `scan` | ✅ | 39 hotspots, V1 supported. Required PATH injection for target venv |
| `plan` | ✅ | 1 task, 1 file. $0.01369 (Claude Sonnet 4-6, 788 in / 755 out) |
| `preview` | ✅ | `status == "previewed"`, `overall_passed == true` |

## Pipeline Metrics

| Metric | Value |
|---|---|
| Total time | ~2 min (scan 5s + plan 30s + preview ~90s) |
| LLM cost | $0.01369 (plan only; executor used free tier) |
| Final status | `previewed` |
| overall_passed | `true` |
| Files modified | 1 (`app/schemas/philosophy.py`) |
| Lines modified | +3 / -3 (6 lines) |

## Product Metrics

| Metric | Value |
|---|---|
| Did the patch resolve exactly the issue? | YES |
| Were there changes outside scope? | NO |
| Was it applied without human edits? | YES |
| Would the diff be accepted in a real PR? | YES |

## Human Edits

| Type | Count |
|---|---|
| Formatting | 0 |
| Type hints | 0 |
| Wrong file | 0 |
| Logic | 0 |
| Tool workaround (CRLF) | 0 |
| **Total** | **0** |

## CRLF Verification

```
python -c "d=open('patch.diff','rb').read(); print(d.count(b'\r\n'))"
# Output: 0

python -c "print(b'\r\n' in open('patch.diff','rb').read())"
# Output: False
```

Target repo `core.autocrlf=true` — confirms the fix works even with Windows Git's default
CRLF translation enabled.

## Validation Details

- **ruff:** All checks passed (return code 0)
- **pytest:** 134 passed, 3 skipped, 2 warnings. Coverage 81.58% (above 80% threshold)
- **git apply --check:** Passed (implicit — validation workspace applies the patch internally)

## Generated Patch

```diff
--- a/app/schemas/philosophy.py
+++ b/app/schemas/philosophy.py
@@ -6,10 +6,10 @@
     Schema representing an inspirational philosophy item.
     """

-    id: str
-    name: str
+    id: str = Field(..., description='Unique identifier', max_length=50)
+    name: str = Field(..., description='Name of the philosopher', max_length=100)
     role: dict[str, str] = Field(..., description="Role or title in multiple languages")
-    image_url: str
+    image_url: str = Field(..., description="URL of the philosopher's image")
     description: dict[str, str] = Field(
         ..., description="Detailed description in multiple languages"
     )
```

## Comparison with Dogfooding 002

| Metric | Dogfooding 002 | Dogfooding 003 |
|---|---|---|
| Same issue | ✅ | ✅ |
| Same target | ✅ | ✅ |
| Patch semantically correct | ✅ | ✅ |
| `overall_passed` | ❌ (CRLF) | ✅ |
| Human edits required | 1 (CRLF workaround) | 0 |
| Status | `validation_failed` | `previewed` |

## Verdict

```
Issue:
Add Field() metadata to PhilosophyItemSchema (id, name, image_url)

Pipeline reliability:
PASS — fully automatic cycle, zero manual intervention

Patch quality:
PASS — diff semantically correct, scope exact, QA green

Would I merge this PR exactly as generated?
YES — no edits of any kind needed

Reason:
The CRLF fix (PR #193, newline="" on all write paths) resolved the only failure
mode from Dogfooding 002. The pipeline now completes a fully automatic cycle on
Windows with core.autocrlf=true. This is PatchForge's first confirmed end-to-end
success on Windows.
```

## Lessons

1. The CRLF fix is confirmed working on Windows with `core.autocrlf=true`
2. The `--risk-budget medium` flag and PATH injection are still required for this target
3. The plan quality is consistent: 1 task, 1 file, zero scope creep across both experiments
4. Cost is minimal: $0.01369 for the plan (executor used free tier)

## Recommendation

Run Dogfooding 004 with a slightly more complex issue (2-3 files) against the same or a
different target to confirm reliability across multiple scenarios. Two consecutive successes
provide evidence that PatchForge is reliable in real scenarios, not just trivial cases.
