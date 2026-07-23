# Phase 2 pre-PR diff review

## Scope

Branch: `feat/issue-282-validator-plugins-phase-2`  
Base: `main` (`51f8480`)  
Commits: `53af3fc`, `c352a57`, `3e0e2e1`, `66032b3`

This diff implements the execution-contract slice of issue #282 Phase 2. It
does not close issue #282 and does not include Phase 3 result versioning or
authorization decisions.

## Files reviewed

- `src/orchestrator/schemas/validator_output.py`: compatible V2 metadata,
  terminal states, coverage states, declaration identity and global status.
- `src/orchestrator/agents/validator/process.py`: raw process results,
  shell-free execution and timeout cleanup for the managed process tree.
- `src/orchestrator/agents/validator/adapters.py`: ordered V2 execution,
  adapter command resolution, success-code evaluation and coverage aggregation.
- `src/orchestrator/agents/validator/__init__.py`: V2/legacy dispatch boundary.
- `src/orchestrator/validation_workspace.py`: atomic final artifact write.
- `tests/test_validator_v2.py`: V2 identity, order, state, coverage and
  compatibility scenarios.
- `docs/adr/ADR-0010-validator-configuration-contract.md` and
  `docs/planning/p5/01-validator-plugins.md`: Phase 2 contract and boundaries.

## Invariants checked

- `validators is None` continues through the existing legacy runners.
- V2 results carry `result_profile="v2"`, `validator_id`, adapter and declared
  order; historical records without a profile remain readable as V1.
- V2 success is evaluated only by the aggregator using each declaration's
  `success_codes`; legacy `(0, 5)` behavior is unchanged.
- Only an approved declaration allows the next declaration to run. Remaining
  declarations are persisted as `not_run`.
- Standard fixed adapters may report `verified`; `command`, `tox` and all
  command overrides report `declared_only`.
- `unavailable` and cleanup failure produce `incomplete`, never approval.
- The final `validation.json` replacement is atomic; an interrupted write does
  not produce a valid partial result.

## Validation performed

- `uv run ruff check .` — passed.
- `uv run ruff format --check .` — passed.
- Directed validator tests — 83 passed.
- Full suite: `uv run pytest tests/ -q -n auto` — 1,031 passed, 6 skipped.
- `git diff --check` — passed.

## Known limitations and explicit follow-up

- Windows cleanup currently uses a new process group plus `taskkill /T /F`.
  This supervises the ordinary process tree but is not a native Job Object
  implementation and cannot control deliberately detached processes. A native
  Job Object backend remains a follow-up if stronger Windows cleanup guarantees
  become required.
- `prepared` and `running` are internal states in Phase 2. Only terminal
  results are persisted; crash recovery is represented by a missing final
  artifact and is non-authorizable.
- Formal `validation.json` versioning, V1/V2 migration policy beyond the
  profile discriminator, and `ValidationDecision` remain Phase 3.
- Sandbox, hermetic execution, dynamic plugin discovery, doctor integration,
  candidate promotion and end-to-end preview/apply/CI policy changes remain
  outside this PR.

## Pre-PR disposition

The diff is internally consistent with the documented Phase 2 boundary and
passes the available validation. The Windows cleanup limitation above must be
accepted explicitly by reviewers or addressed in a follow-up before treating
the process-supervision guarantee as stronger than best effort.
