# Doctor reads orchestrator.json multiple times per run

## Symptom

Running `src/orchestrator/doctor.py:check()` reads and parses
`orchestrator.json` from disk multiple times within a single invocation.
`check()` calls `check_workspace()`, `check_ruff()`, and `check_pytest()`
sequentially, and each of these sub-checks independently opens and parses
`orchestrator.json` on every call. For one `check()` run this means the same
config file is read from disk three separate times.

## Desired Outcome

`orchestrator.json` should be read once per `check()` invocation and reused
across all sub-checks, instead of being re-read and re-parsed by each
sub-check independently.
