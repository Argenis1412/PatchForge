# Experiment: Dogfooding 013 — post-#282 deterministic operational run

**Date:** 2026-07-28/29
**Mode:** deterministic; no LLM provider calls
**Source:** `origin/main` at `27cd04988f19ad25b524a770abade53e3071adb1`

## Purpose

Exercise the recent operational boundaries without consuming Claude or Gemini
credits: `doctor`, `scan`, V2 validation, timeout policy, deterministic
`preview`, and candidate promotion. This was not a test of `plan` or of LLM
patch quality.

## Setup

- `git fetch origin main` completed before cloning. The disposable clone was
  checked out at the source SHA above.
- The target configuration used `schema_version: "2.0"` and explicit timeout
  fields. The runtime was created outside the target at
  `C:\tmp\patchforge-dogfooding-013-20260728\runtime`; the target contained no
  `.venv` during the successful V2 runs.
- `preview` received a persisted `plan.json` plus an `execution_plan.json`.
  Its single deterministic edit added a non-functional HTML comment to
  `README.md`; it did not invoke the LLM executor.

## Results

### Readiness and deterministic execution

`doctor --json` reported `support_profile: "v2"` and found the declared
`ruff` and `pytest` adapters through PatchForge's external runtime. `scan`
persisted runs successfully without provider credentials.

The final successful run was `run_20260729_000931_0ca2ad`. With the V2 `ruff`
declaration, preview produced `patch.diff` and an authorized V2
`validation.json`:

- `result_profile: "v2"`
- `authorization_profile: "v2_verified_roles@1"`
- `lint` coverage: `verified`
- deterministic executor metadata: `model_used:
  "deterministic-execution-plan-v2"`

`apply` promoted candidate commit
`4ff83e74d18d3fe089028f4a72afb7d7d1bf7565`. Both
`refs/heads/patchforge/run_20260729_000931_0ca2ad` and
`refs/patchforge/promotions/run_20260729_000931_0ca2ad` resolve to that
commit. The caller stayed on `dogfooding-013-base` at
`cc0f1af16f9672701c01d383fa694e2d9ab8b886`, and its `README.md` did not
contain the candidate marker.

An earlier apply attempt on a detached checkout was rejected with
`candidate promotion requires an attached base branch`. Creating the temporary
base branch and rescanning satisfied that protocol precondition; no mutation
occurred before it was satisfied.

### Timeout control

Run `run_20260729_001139_df1eea` declared a V2 `command` validator that sleeps
for five seconds and set `validator_run` to one second. Preview failed closed:

- the record has `status: "timeout"`, `timed_out: true`, and
  `return_code: -1`;
- the V2 decision is not authorized;
- the persisted summary advises increasing `--validator-timeout`;
- neither the candidate ref nor promotion receipt exists for the run.

This verifies the operational timeout policy and that failed validation cannot
reach promotion.

### Pytest V2 observation

A V2 profile declaring the standard `pytest` adapter did not produce an
authorizable result in this self-clone. The adapter runs `pytest` against the
candidate root explicitly, so the full repository suite ran despite a fixture
`testpaths` setting. Four CI tests failed under the temporary V2 target
configuration, and the validator also recorded `Validation workspace changed
during V2 validation`; both outcomes failed closed. No candidate or receipt
was published for those runs.

This is evidence about the current standard-`pytest` V2 operational behavior,
not evidence of an LLM problem or a reason to alter the current priority. A
future scoped issue can decide whether standard pytest validation needs a
different isolation strategy; no product code was changed here.

## Verdict

**PARTIAL PASS.** The no-LLM path verified V2 doctor diagnostics, deterministic
scan/preview, external-runtime adapter resolution, fail-closed timeout policy,
and authorized candidate promotion with receipt recovery while preserving the
caller checkout. The combined `ruff` + standard `pytest` V2 profile remains
non-authorizable in this self-clone and should be investigated only when
evidence justifies a dedicated issue.

The full LLM dogfooding run remains deferred until Claude credits return; its
purpose remains learning from a small real task, not re-proving this controlled
path.

## Post-review authority note

The manually persisted `execution_plan.json` used by this experiment was a
controlled operational fixture, not a provenance-bound refinement of
`plan.json`. It remains historical evidence only; current pipeline consumers
reject such artifacts until a separately designed authorized compiler exists.
