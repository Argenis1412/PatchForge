# Experiment: Dogfooding 006 — E2E validation of D-001 root cause fix (file_collector)

**Date:** 2026-07-08
**Target:** `Clon_PatchForge_Proper` — PatchForge codebase at `9e5604b` (post-PR-#200)
**Issue:** Add per-task structured observability events to the Executor agent
**Run ID:** `run_20260708_012619_23cbbb`
**Provider:** claude-sonnet-4-6 for plan; OpenRouter free tier + Gemini 2.5 Flash for executor

## Purpose

Validate that the D-001 root cause fix (PR #200, `file_collector` module) eliminates phantom
path hallucinations in the Architect. Previous runs (004, 005) used a single-file issue where
the architect repeatedly hallucinated `tests/test_risk.py`. This run uses a multi-file behavioral
change to stress the scope-control invariant while measuring whether the `[TARGET FILES]` block
prevents phantom paths.

Two secondary objectives:
1. Measure token/cost impact of the `[TARGET FILES]` block vs. previous runs.
2. Validate that the 500-path cap does not truncate PatchForge's own repo.

Design note: the issue file deliberately does NOT enumerate exact filenames. The architect must
locate the relevant files from the `[TARGET FILES]` listing. This is the critical control for
validating D-001 — if the issue named the files, the architect could copy them without using the
listing.

## Locations

```text
PatchForge : C:\Users\Visitante\Desktop\Projectos de Github\PatchForge\
Target     : C:\Users\Visitante\Desktop\Projectos de Github\Clon_PatchForge_Proper\
Issue file : docs/experiments/dogfooding-006-issue.md
Workspace  : C:\Users\Visitante\.cache\patchforge\workspaces\1aa57e02233a\
```

## Setup

Clone synced to `9e5604b` (merge commit pulling PR #200 into the clone's main). Ran `uv sync`
in the clone — all 50 packages already resolved, no installs needed. PatchForge venv activated
before scan. Risk budget set to `medium` for the first scan; re-scanned with `high` after the
architect planned 7 files (exceeding medium's `max_files=5`).

## Pipeline Results

| Step | Result | Detail |
|------|--------|--------|
| `scan` (medium) | ✅ | 71 hotspots. Run ID `run_20260708_012431_26ba46`. Blocked: plan risk gate (7 files > max_files=5) |
| `scan` (high) | ✅ | 71 hotspots. Run ID `run_20260708_012619_23cbbb`. Risk budget high allows 7 files |
| `plan` | ✅ | 7 tasks, 7 files. 195/195 paths injected (truncated=False). $0.05405 (claude-sonnet-4-6, 3233 in / 2957 out) |
| `preview` | ✅ CORRECT FAILURE | T7 file-not-found. Ruff: syntax error in providers.py (T2 mangled comment). `executor_had_errors=true`, `status=validation_failed` |
| `apply` | ✅ BLOCKED | "Patch validation failed during preview" — correctly rejected |

## D-001 Validation Analysis

### Phantom path checkpoint (primary objective)

After the plan step, every `files_to_modify` entry was checked against the clone filesystem:

| Task | File | Exists? | Notes |
|------|------|---------|-------|
| T1 | `src/orchestrator/agents/executor/scheduler.py` | ✅ EXISTS | Wrong target — see below |
| T2 | `src/orchestrator/agents/executor/providers.py` | ✅ EXISTS | Syntax error introduced |
| T3 | `src/orchestrator/agents/executor/logging.py` | ✅ EXISTS | NOOP task (correct) |
| T4 | `src/orchestrator/commands/apply.py` | ✅ EXISTS | Wrong caller — apply doesn't call executor |
| T5 | `src/orchestrator/commands/preview.py` | ✅ EXISTS | Correct change (`run_dir` threading) |
| T6 | `src/orchestrator/commands/plan.py` | ✅ EXISTS | NOOP audit (correct) |
| T7 | `tests/test_executor_observability.py` | PHANTOM (new-file intent) | Parent `tests/` exists — valid new-file creation intent |

**Result: Zero phantom paths for existing files. D-001 fix validated.**

T7 is new-file creation with a valid parent directory — the correct classification under
`validate_plan_paths()` (parent exists → new-file intent, not phantom). The executor cannot
create new files, which caused the T7 failure, but this is a known PatchForge limitation,
not a D-001 regression.

Comparison with dogfooding-004/005: in both previous runs the architect planned
`tests/test_risk.py` (phantom — parent `tests/` exists but the specific file name was wrong).
In this run, zero path hallucinations for existing files. The `[TARGET FILES]` block grounded
the architect in the real file tree.

### Architect logic quality (secondary observation)

While paths are no longer hallucinated, the architect still made functional errors:

- **T1 (scheduler.py)**: The architect confused `scheduler.py` (DAG builder, no `run()`) with
  `executor/__init__.py` (the actual entrypoint with the task loop). The executor wrote LLM
  tool-call markup into scheduler.py instead of code, completely gutting the file. This is a
  consequence of the architect not understanding the module structure — it found the right
  directory but the wrong file within it.

- **T2 (providers.py)**: Cosmetic blank-line cleanup introduced a syntax error: a multi-line
  comment (`# HIGH risk has no fallback by policy: ...`) was mangled into `_HIGH risk has no
  fallback by policy:`, turning a comment into invalid Python.

- **T4 (apply.py)**: apply.py does not call the executor. The architect incorrectly identified
  it as a caller. The change (adding `run_dir` to `run_validator()`) was also wrong —
  `run_validator` takes different parameters.

- **T5 (preview.py)**: Correctly added `run_dir=run_dir` to the `executor_agent.run()` call.
  This is the only task that produced a semantically correct diff.

- **T6 (plan.py)**: Correctly audited that plan.py does not call the executor and made no
  changes.

- **Missing**: `ci.py` — the second actual caller of `executor_agent.run()` — was never
  included in the plan. The issue said "all callers that supply a run_dir" but the architect
  only found one.

**Takeaway**: D-001 fix prevents path hallucinations but does not prevent logical errors in
file selection within the real file tree. "Correct path" ≠ "correct target for the change."

## Token / Cost Impact Analysis

| Metric | Dogfooding 005 | Dogfooding 006 | Delta |
|--------|---------------|----------------|-------|
| Input tokens (plan) | 910 | 3233 | +2323 (+255%) |
| Output tokens (plan) | 1117 | 2957 | +1840 (+165%) |
| Cost (plan) | $0.01948 | $0.05405 | +$0.03457 (+177%) |
| `[TARGET FILES]` paths | N/A (fix not deployed) | 195/195 | — |
| Truncated | N/A | False | — |

The input token increase (+2323) is driven by the `[TARGET FILES]` block. At ~195 paths of
~10 characters each plus newlines, the block contributes roughly 2000-2200 tokens. This matches
the observed delta. The output token increase (+1840) reflects a more complex plan (7 tasks vs.
2 tasks for the previous simple issue).

The cost increase ($0.03457) is meaningful but acceptable for the correctness gain. A 2.7× cost
increase in the plan step prevents phantom-path failures that waste the entire executor budget.

The 500-path cap is non-binding: PatchForge clone has ~195 files in the scanned tree.
Truncation cannot be tested with this target. Remains a risk for repos > 500 files.

## Pipeline Metrics

| Metric | Value |
|---|---|
| Total time | ~4m (scan ~1s + plan ~90s + preview ~2m) |
| LLM cost (plan) | $0.05405 (claude-sonnet-4-6, 3233 in / 2957 out) |
| LLM cost (executor) | $0.0 (free tier) |
| Final status | `validation_failed` |
| `executor_had_errors` | `true` (T7 file-not-found) |
| `overall_passed` (validator) | `false` (ruff syntax error in providers.py) |
| Files modified by executor | 4 (T2 providers.py, T4 apply.py, T5 preview.py — T1 scheduler.py corrupted) |
| Hotspot count | 71 (vs 69 in D005, +2 from PR #200 files) |
| `[TARGET FILES]` paths injected | 195 of 195 (truncated=False) |

## Generated Patch

The patch contained diffs for scheduler.py (catastrophic), providers.py (syntax error),
apply.py (wrong target), and preview.py (correct):

```diff
--- a/src/orchestrator/agents/executor/scheduler.py
+++ b/src/orchestrator/agents/executor/scheduler.py
@@ -1,49 +1,5 @@
 [entire file deleted, replaced with LLM tool-call markup artifact]

--- a/src/orchestrator/agents/executor/providers.py
+++ b/src/orchestrator/agents/executor/providers.py
 [style cleanup; line 30 syntax error: comment mangled into code]
 -# HIGH risk has no fallback by policy: if Claude is unavailable...
 +_HIGH risk has no fallback by policy: # if Claude is unavailable...

--- a/src/orchestrator/commands/apply.py
+++ b/src/orchestrator/commands/apply.py
@@ -396,7 +396,7 @@
-                post_val_output, _ = run_validator(config=config)
+                post_val_output, _ = run_validator(config=config, run_dir=run_dir)

--- a/src/orchestrator/commands/preview.py
+++ b/src/orchestrator/commands/preview.py
@@ -176,6 +176,7 @@
                 config=config,
                 staging_dir=staging_dir,
                 force_provider=force_provider,
+                run_dir=run_dir,
```

Validation: ruff failed (syntax error in providers.py). pytest could not collect due to the
syntax error. The patch was correctly blocked.

Would I merge the generated patch as-is? **NO**:
- scheduler.py is catastrophically corrupted
- providers.py has a syntax error
- apply.py change is semantically wrong (wrong target)
- The core change — `run_dir` threading in executor/__init__.py — was never written at all
- Only preview.py is semantically correct; it would need cherry-picking

## Comparison with Dogfooding 005

| Metric | Dogfooding 005 | Dogfooding 006 |
|---|---|---|
| Target commit | `cd6b689` (post-PR-#195) | `9e5604b` (post-PR-#200) |
| Issue type | Single-file (risk.py) | Multi-file (executor observability) |
| Hotspots | 69 | 71 (+2 from PR #200) |
| Phantom paths in plan | 1 (`tests/test_risk.py`) | 0 (all files exist or valid new-file intent) |
| `[TARGET FILES]` injected | N/A (fix not deployed) | 195/195 (truncated=False) |
| Input tokens (plan) | 910 | 3233 (+255%) |
| Plan cost | $0.01948 | $0.05405 (+177%) |
| `executor_had_errors` | `true` | `true` |
| `status` after preview | `validation_failed` | `validation_failed` |
| Panel | ✘ red | ✘ red |
| Apply | Blocked (correct) | Blocked (correct) |
| D-001 fix exercised | Not deployed | ✅ Validated |

## New Findings

| ID | Severity | Description |
|---|---|---|
| D-005 | Medium | Architect confuses submodule files: found correct executor package directory but targeted `scheduler.py` instead of `__init__.py`. The `[TARGET FILES]` block lists all files but provides no structural context about which file contains the main entrypoint. |
| D-006 | Low | Executor generates LLM tool-call markup as file content when confused about file structure. T1 replaced all of `scheduler.py` with a tool-call XML fragment. No validation catches this before ruff. |

## Verdict

```text
Issue:
Add per-task structured observability events to the Executor agent.

D-001 (phantom path rejection — root cause fix):
VALIDATED ✅ — Zero phantom paths for existing files. The [TARGET FILES] block (195/195 paths,
truncated=False) grounded the architect in the real file tree. All six existing files in the plan
were correctly found. Compared to dogfooding-004/005 where tests/test_risk.py was hallucinated
in every run, this is a clear regression fix.

Remaining D-001 risks:
- Correct paths, wrong targets (D-005): architect can pick the wrong file within a package.
  [TARGET FILES] tells the architect WHAT exists, not WHY or WHAT EACH FILE DOES.
- Executor creates tool-call markup instead of code when confused (D-006).
- 500-path cap untested: PatchForge clone has ~195 files, well under cap.

Pipeline reliability (overall):
PARTIAL — D-001 fix works. Pipeline correctly caught all failures:
executor_had_errors=true, validation_failed, apply blocked. D-003 fix holds.
The patch would have been catastrophic (scheduler.py gutted) if applied.

Would I merge the generated patch as-is?
NO — scheduler.py catastrophically corrupted, providers.py syntax error, apply.py wrong target.
Only preview.py change ($run_dir threading) is semantically correct — a 1/6 hit rate.
```

## Lessons

1. **D-001 root cause fix works.** The `[TARGET FILES]` block eliminated phantom path hallucinations
   entirely. In three prior runs with a different issue, the architect hallucinated the same wrong
   filename. In this run: zero phantom paths for existing files.

2. **"Correct path" ≠ "correct target."** The architect found `scheduler.py` (real file in the
   executor package) but it is not the file that contains the task loop. The file listing tells the
   architect what exists, not what each file does. For packages with multiple submodules, the
   architect needs more structural context than a flat path list.

3. **Executor generates tool-call markup when it can't read a file it's supposed to modify.**
   T1 wrote an XML tool-call fragment into scheduler.py instead of actual code. This appears when
   the executor's internal prompt asks for a read before write, but the read tool is unavailable
   at execution time. This is a distinct failure mode from T7 (file-not-found) — the diff is
   generated but the content is garbage.

4. **Risk budget escalation is a signal.** The architect planned 7 files for a 2-file issue.
   The medium budget correctly blocked this (max_files=5). Re-scanning with high budget revealed
   the over-scoping. The risk gate is working as designed: it prevents over-broad patches from
   reaching preview.

5. **The multi-file issue design worked for D-001 validation.** Because the issue file did not
   enumerate exact filenames, we can confirm that the architect used the `[TARGET FILES]` listing
   to find files rather than copying them from the issue text.

## Follow-up Actions

| ID | Priority | Action |
|---|---|---|
| D-005 | Medium | Give architect structural context within packages (e.g. a `__all__` or function-index summary per submodule) to prevent correct-path/wrong-file errors |
| D-006 | Medium | Executor should validate that generated file content is valid Python syntax before writing the diff; reject and flag tasks that produce markup artifacts |
| — | Low | Dogfooding-007: test D-001 on a repo > 500 files to validate truncation behavior and alphabetical bias |
