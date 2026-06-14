# CS-003: Add `--risk-budget` flag to `patchforge scan`

## Metadata

| Field | Value |
|-------|-------|
| **Issue** | `issues/003-risk-budget-flag.md` |
| **Target repo** | PatchForge (Clon_PatchForge) |
| **Date** | 2026-06-14 |
| **Experiment** | 003 — Risk budget flag |
| **Branch (clone)** | `main` (Clon_PatchForge) |
| **Branch (original)** | `feat/experiment-003-risk-budget-flag` |

## The ironic loop

The problem being fixed: risk budget defaults (`max_files=2`, `risk_budget="low"`)
blocked Experiment 002's refactor, forcing manual `run.json` edits.

To fix this... we had to manually edit `run.json` again in Experiment 003, so the
AI-generated plan would pass the risk gate. PatchForge fixed its own risk gate
by temporarily bypassing the risk gate.

## Results

| Metric | Value |
|--------|-------|
| Files modified | 3 (`main.py`, `scan.py`, `test_scan.py`) |
| Lines added | 118 |
| Lines removed | 5 |
| Tests executed | 291 |
| Tests passed | 291 |
| Ruff | 0 errors |
| LLM cost | ~$0.034 (1 plan run) |
| Human time | ~30 min (supervision + debugging 3 bugs) |
| Pipeline time (scan → apply) | ~2 min (plan) + ~15 min (debugging) |

## Bugs discovered during experiment

| # | Bug | Impact | Fix |
|---|-----|--------|-----|
| 1 | LLM-generated code exceeded ruff 100-char line limit | Validation failed — patch not applied | Manually split long option line and docstrings |
| 2 | Test assertion expected different error message format | pytest failed — test asserted one string, code produced another | Matched assertion to actual output format |
| 3 | PowerShell `Set-Content -Encoding UTF8` adds BOM (U+FEFF) | Pydantic `model_validate_json` crashed with `json_invalid` | Used .NET `File.WriteAllText` with `UTF8Encoding(false)` |

## Lessons

1. **Self-patching works** — PatchForge generated a correct plan that modified
   all 3 required files with accurate logic. The only problems were formatting
   (E501) and test assertion mismatch.
2. **LLMs don't count columns** — The generated code consistently exceeded the
   100-char line limit. This is an expected failure mode for AI-generated code
   in ruff-enforced projects.
3. **PowerShell + BOM = silent corruption** — The UTF-8 BOM issue is
   platform-specific but devastating (corrupts JSON parsing in Pydantic).
4. **Validation catches real issues** — The failed preview prevented a broken
   patch from being applied, proving the validation gate works.

## Timeline

| Milestone | Duration |
|-----------|----------|
| Write issue file | ~2 min |
| git pull + merge conflicts | ~3 min |
| scan → workaround (edit run.json) | ~2 min |
| plan (LLM) | ~2 min |
| preview (failed — E501 + test assertion) | ~2 min |
| Debug + fix code manually | ~15 min |
| ruff + pytest verification | ~1 min |
| **Total** | **~27 min** |
