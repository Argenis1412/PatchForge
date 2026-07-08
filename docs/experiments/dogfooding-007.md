# Experiment: Dogfooding 007 — E2E validation of D-005 + D-006 (structural annotations + ast.parse guard)

**Date:** 2026-07-08
**Target:** `Clon_PatchForge_Proper` — PatchForge codebase at `8a622e2` (post-PR-#203)
**Issue:** Add Claude to the validator's provider fallback chain
**Run ID:** `run_20260708_205400_bb045a`
**Provider:** claude-sonnet-4-6 for plan; Gemini 2.5 Flash + OpenRouter for executor/validator

## Purpose

Validate D-005 (PR #203, structural annotations in `[TARGET FILES]`) and D-006 (PR #202,
`ast.parse` guard in executor) working together in a real pipeline run.

- **D-005 control condition**: The issue deliberately does NOT name `summarizer.py`. The architect
  must locate the correct file from the annotated `[TARGET FILES]` block. D-005 is confirmed if
  the architect targets `summarizer.py` (which exposes `_summarize_errors()`) rather than another
  file in the `validator/` package.
- **D-006 control condition**: T1 modifies `summarizer.py` (a `.py` file inside a Python package).
  D-006 is confirmed if the diff is generated without being rejected — meaning `ast.parse()`
  accepted the modified file as valid Python.

The task itself (add Claude to validator's fallback chain) targets the `validator/` package which
has 4 `.py` files (`__init__.py`, `summarizer.py`, `runners.py`, `logging.py`). With D-005 active,
all four receive structural annotations.

## Locations

```text
PatchForge : C:\Users\Visitante\Desktop\Projectos de Github\PatchForge\
Target     : C:\Users\Visitante\Desktop\Projectos de Github\Clon_PatchForge_Proper\
Issue file : docs/experiments/dogfooding-007-issue.md
Workspace  : C:\Users\Visitante\.cache\patchforge\workspaces\1aa57e02233a\
```

## Setup

Clone synced to `8a622e2` (merge of main after PRs #200–#203: D-001, D-006, docs, D-005).
Ran `uv sync` in the clone — all 50 packages resolved, no installs needed.
PatchForge venv + clone venv both in PATH before scan to ensure `V1 supported: yes`.

## Pipeline Results

| Step | Result | Detail |
|------|--------|--------|
| `scan` (medium) | ✅ | 71 hotspots. 198/198 paths. `V1 supported: yes` |
| `plan` | ✅ | 2 tasks, 2 files. 198/198 paths injected (truncated=False). $0.03671 (claude-sonnet-4-6, 5311 in / 1385 out) |
| `preview` | ⚠️ VALIDATION_FAILED | T2 executor error (new-file creation). T1 diff generated (D-006 ✅). Ruff: import sort. Pytest: 1 failure (CB called twice) |

## D-005 Validation Analysis

### Annotations in `[TARGET FILES]`

198/198 paths injected, `truncated=False`. D-005 annotated all `.py` files in the
`validator/` package. The four relevant annotations visible to the architect:

```
src/orchestrator/agents/validator/__init__.py  # Validate changed files against the target repo. | run()
src/orchestrator/agents/validator/logging.py   # _get_logger()
src/orchestrator/agents/validator/runners.py   # RunnerResult, run_ruff(), run_pytest()
src/orchestrator/agents/validator/summarizer.py  # LLM-based error summarizer for validation failures with provider fallback. | _summarize_errors()
```

### Architect target selection

| Task | File targeted | Correct? | Notes |
|------|--------------|---------|-------|
| T1 | `src/orchestrator/agents/validator/summarizer.py` | ✅ | `_summarize_errors()` annotation was the decisive signal |
| T2 | `tests/test_validator_summarizer.py` | ✅ | New file; correct parent dir |

**Result: D-005 confirmed.** The architect correctly identified `summarizer.py` from annotations
alone. The module docstring annotation `# LLM-based error summarizer…` combined with
`_summarize_errors()` uniquely distinguished it from `__init__.py` (`run()`) and `runners.py`
(`run_ruff()`, `run_pytest()`). No phantom paths.

## D-006 Validation Analysis

T1 modified `summarizer.py`. The patch was generated (not rejected with `ERROR` status).
This confirms D-006's `ast.parse` guard accepted the modified file as syntactically valid Python.

The T2 failure was an executor-level "File not found" error (the target file `test_validator_summarizer.py`
did not exist yet) — not a D-006 rejection. This is the same new-file-creation limitation observed in
dogfooding-006 (T7).

**Result: D-006 happy path confirmed.** Modified Python file passed `ast.parse` validation;
diff was generated and stored in `patch.diff`.

## Validation Failures Analysis

The preview returned `validation_failed` due to two unrelated issues in T1's implementation:

### Ruff: import sort violation

The executor added `from orchestrator.clients.anthropic_client import get_anthropic_client`
after `from orchestrator.schemas.validator_output import ToolResult`, violating isort order
(third-party/first-party ordering). Fixable with `ruff --fix`.

### Pytest: `test_validator_uses_raw_stderr_when_cb_open` — CB called twice

The LLM implemented the Claude fallback with a new manual try/except block that reuses
`_cb_validator` (the validator's Gemini circuit breaker) for the Claude call:

```python
from orchestrator.agents.validator import _cb_validator
response = _cb_validator.call(lambda: client.messages.create(...))
```

This caused `cb_mock.call.assert_called_once()` to fail: the mock was called twice (once
for Gemini, once for Claude). The correct implementation was to extend the existing
`_call_chain([_call_openrouter], ...)` call to `_call_chain([_call_openrouter, _call_claude], ...)`.
One extra argument, zero new try/except blocks, zero CB reuse.

Root cause: the LLM over-engineered the solution by copying the Gemini pattern instead
of recognizing that `_call_chain` already handles multi-provider orchestration. The AC and
issue description did not explicitly say "extend the existing `_call_chain` call" — that
knowledge required reading `summarizer.py` carefully enough to notice the existing chain.

## Discoveries

### D-007a — Executor cannot create new files (confirmed again)

- **File:** `src/orchestrator/agents/executor/`
- **Behaviour:** When `files_to_modify` lists a path that does not exist, the executor
  fails immediately with "File not found" and sets task status to `ERROR`.
- **Impact:** The architect correctly plans new test files (T2, T7 in D-006) but the
  executor rejects them silently. New-file-creation requires a dedicated executor path.
- **First seen:** dogfooding-006 (T7). Confirmed again here (T2).
- **Why not fixed yet:** New-file creation requires a new executor code path (write from
  scratch vs. read-diff-apply). Tracked separately.

### D-007b — LLM reuses wrong CB when adding a new provider fallback

- **File:** `src/orchestrator/agents/validator/summarizer.py`
- **Behaviour:** When asked to "add Claude as fallback," the executor LLM copied the Gemini
  try/except pattern and reused `_cb_validator` for the Claude call instead of extending
  the existing `_call_chain` invocation. The manual approach is harder to implement correctly
  and breaks existing tests that assert CB call counts.
- **Root cause:** The issue description says "extend the fallback chain" but doesn't explicitly
  say "extend the existing `_call_chain(...)` call." The LLM's prior is to add a new block,
  not to recognize the minimal edit.
- **Lesson:** For issues where the correct fix is a minimal change to an existing call (not a
  new block), the issue AC should name the construct to extend: "modify the existing
  `_call_chain(...)` call in `_summarize_errors()` to include `_call_claude`."

## D-005 + D-006 Validation Verdict

| Check | Result |
|---|---|
| `[TARGET FILES]` contains structural annotations for all `validator/` `.py` files | ✅ D-005 |
| Architect targeted `summarizer.py` (not `__init__.py` / `runners.py`) | ✅ D-005 |
| T1 diff generated without `ERROR` rejection | ✅ D-006 happy path |
| Modified `summarizer.py` in diff is syntactically valid Python | ✅ D-006 |
| `preview` outcome | ⚠️ `validation_failed` — unrelated to D-005/D-006 |

Both D-005 and D-006 are validated. The `validation_failed` outcome reflects an implementation
quality issue (wrong CB reuse), not a defect in the fixes under test.
