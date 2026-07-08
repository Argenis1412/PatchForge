# Issue: Add per-task structured observability events to the Executor agent

## Severity
medium

## Problem

The Executor agent is the only pipeline agent that does not emit structured
`log_event()` calls. It uses Python's `logging` module to write unstructured
text to a log file, but does not write to the structured observability system
(`pipeline.jsonl` and per-run `events.jsonl`).

The preview command emits stage-level events around the executor call, but no
per-task events are recorded. When a task starts, completes, or fails inside the
executor, the only structured record is the aggregated summary returned at the end.

This means per-task timing and per-task provider selection are absent from the
structured audit trail. Debugging multi-task failures requires reading unstructured
logs instead of querying events.

## Acceptance Criteria

- [ ] Before each task executes, the executor emits a structured event with
  `event="task_start"` containing at minimum `task_id`, `risk_level`, and
  `files_to_modify` in the data payload
- [ ] After each task completes, the executor emits a structured event with
  `event="task_end"` containing at minimum `task_id`, `status`, `tokens_used`,
  and `cost_usd` in the data payload
- [ ] Events use `trace_id=run_id` (same convention as the rest of the pipeline)
- [ ] Per-run events land in `events.jsonl` inside the run directory (not only in
  the global `pipeline.jsonl`)
- [ ] The `run()` function accepts an optional `run_dir` parameter (Path or None)
  that is forwarded to `log_event()`; when None, only `pipeline.jsonl` is written
- [ ] All callers that already supply a `run_dir` (commands that run the executor
  as part of a pipeline) pass it through so per-run events are captured
- [ ] `ruff check .` passes with 0 errors
- [ ] `pytest` passes (existing tests must not be deleted or modified to pass)
- [ ] New tests cover the two new event emissions

## Scope

Limit changes to the executor agent module and the command modules that call it.
Do not touch the observability module, the architect, the validator, or the scanner.
Do not modify or delete any existing test file.
