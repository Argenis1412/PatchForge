# ADR-0003 Alignment: Isolate Executor Writes & Replace `st_mtime` with Manifest

## Summary

Closes the three most critical gaps between the ADR-0003 product contract and the current implementation:

1.  **Executor writes to staging, not the working tree** — the `Safety Rule` ("Before `apply`, zero target repository modifications") is now respected.
2.  **Deterministic pipeline resume** — `manifest.json` replaces `glob` + `st_mtime` for loading stage outputs.
3.  **Complete project rename** — `uv.lock` now references `orchestrator-core` instead of `agent-lab`.

## Changes

### `src/orchestrator/workspace.py`
- `staging_dir_for_run(run_id)` — returns `outputs/staging/<run_id>`, creates it if missing.
- `read_manifest()` / `update_manifest(stage, filename)` — read/write `outputs/manifest.json`.

### `src/orchestrator/schemas/executor_output.py`
- `FileChange` now includes `original_content` and `modified_content` for full audit trail.

### `src/orchestrator/agents/executor.py`
- `_apply_task()` writes modified files to `staging/<run_id>/<relative_path>` instead of `project_root / <relative_path>`.
- `run()` accepts `staging_dir` parameter (falls back to `outputs/staging/<run_id>` if omitted).
- `FileChange` returns include `original_content` / `modified_content`.

### `src/orchestrator/pipeline.py`
- `_load_stage_output()` reads `manifest.json` instead of `glob(f"{stage}_*.json")` + `st_mtime`.
- `_persist_stage_output()` updates `manifest.json` after writing each stage output.
- `_stage_executor()` creates and passes `staging_dir` to `run_executor()`.

### `uv.lock`
- Package name corrected from `agent-lab` to `orchestrator-core`.

### `tests/test_pipeline.py`
- Resume tests now write manifest entries so `_load_stage_output` can find persisted outputs.

## QA Results

| Check | Status |
|---|---|
| 43 unit tests | ✅ All pass |
| Ruff lint | ✅ No new violations |
| CLI smoke test | ✅ `--help`, `scan`, `run --help` work |
| Scout real run | ✅ 10 findings, `$0.0011`, isolated workspace |
| Pipeline dry-run | ⚠️ Intermittent Gemini JSON parse failure (pre-existing, not a regression) |

## Open Issues (Deferred from this PR)

These gaps remain and should be addressed in follow-up PRs:

| Issue | ADR-0003 Reference |
|---|---|
| **`apply` is still a side effect of `run`** — LOW/MEDIUM risk tasks are generated and "applied" (to staging) in a single pass. The ADR requires `apply` to be a separate command with Git-safe verification. | Lines 176-192, 224 |
| **Run artifact layout** — Current layout is `outputs/<stage>_<run_id>.json`; ADR target is `runs/run_001/{run.json, findings.json, plan.json, patch.diff, validation.json, events.jsonl}`. | Lines 137-147 |
| **Patch as product artifact** — The ADR positions the patch as the unit of value; currently the executor writes individual files to staging rather than generating a unified `patch.diff`. | Lines 49, 72, 222 |
| **Intermittent Scout JSON parsing** — Gemini Flash occasionally returns prose instead of structured JSON. The Scout prompt needs hardening or fallback logic. | N/A (pre-existing) |
