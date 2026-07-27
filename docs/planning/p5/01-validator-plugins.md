# P5 — 1. Validator Plugins

> **GitHub issue:** #282  
> **Status:** Complete — Phase 5 operational integration

## Goal

Make PatchForge validation configurable through deterministic, in-tree validator
adapters. The system does not discover or load third-party plugins.

## Delivery plan

### Phase 1 — Contracts and process preparation

- Keep versionless `orchestrator.json` as the legacy V1 profile.
- Require explicit schema V2 before accepting `validators`.
- Add typed validator declarations (`id`, adapter, roles, command and success
  codes) and reject malformed or unknown configuration.
- Establish one process-preparation contract for future adapter execution and
  doctor diagnostics.

### Phase 2 — Adapters and validation coverage

- Add in-tree adapters for ruff, pytest, tsc, flake8, mypy, pylint, unittest,
  tox and explicitly configured commands.
- Add typed execution states, ordered execution and declared/effective role
  coverage.
- Persist a compatible V2 execution profile with declaration identity,
  fail-closed overall status and terminal execution state. Historical records
  without a profile remain explicitly readable as V1.
- Treat command overrides, `command`, and `tox` as declared-only coverage;
  only fixed standard commands provide verified coverage.
- Stop ordered execution after any non-approved result and record the remaining
  declarations as not run. Process-tree cleanup failure is incomplete and
  never authorizable.

### Phase 3 — Versioned results and authorization decisions

- Version `validation.json`, distinguish verified V2 results from historical
  V1 records and centralize authorization in `ValidationDecision`.
- Preserve new V1 executions through an explicit compatibility authorization
  profile; historical unversioned artifacts are diagnostic only.
- Bind decisions to canonical validation requirements and the validation
  subject. Persisted artifacts are audit evidence, not cross-process authority.

### Phase 4 — Candidate promotion

- Validate an isolated candidate commit before publication and promote it with
  a Git compare-and-swap. No required validation occurs after publication.
- Candidate policy is captured from the unpatched base tree, bound to the
  candidate commit, and recovered through a protocol-scoped promotion receipt.
- CLI candidate promotion uses `candidate_promotion@1`; CI and worker legacy
  WALs are rejected across protocol boundaries until their own migration.

### Phase 5 — Diagnostics and end-to-end integration

- Wire doctor to the shared process contract; document migration and run
  preview/apply/CI regression coverage.

## V1 to V2 migration

Keep a versionless configuration for the legacy V1 `ruff`/`pytest` profile.
To declare validators, add `"schema_version": "2.0"` and an ordered
`validators` list. Standard `ruff`, `pytest`, and `unittest` declarations can
provide verified coverage only when their trusted launcher succeeds. `tsc`,
`tox`, `command`, and command overrides are declared-only evidence and cannot
authorize a required role. V2 uses PatchForge's inherited environment, not a
target-local `.venv`; caches belong outside the validation root and persistent
root writes fail validation.

## Fixed boundaries

- `command` and `tox` roles are operator declarations recorded for
  traceability, not semantic proof that a command runs tests or linting.
- No shell invocation, plugin discovery, marketplace, signatures, allowlists
  or hermetic environment guarantee is in scope.
- A future environment fingerprint is audit data only; it is not an
  authorization condition.
- Versionless configuration remains compatible with the known V1 fields.
  `validators` is V2-only.

## Phase 1 acceptance criteria

- A versionless or explicit V1 configuration retains current command override
  behavior.
- A V2 configuration validates every declared validator and rejects unknown
  adapters, duplicate IDs, malformed commands and invalid role declarations.
- JSON parse errors, unsupported versions and unknown top-level fields fail
  visibly rather than silently falling back to defaults.
- Process preparation gives callers one immutable argv/cwd/environment
  representation without executing through a shell.

## Phase 2 boundaries

Phase 2 extends the current result models with optional V2 metadata rather
than formally versioning `validation.json`; that artifact versioning,
historical-result policy and `ValidationDecision` remain Phase 3 work. Phase 2
does not add sandboxing, hermetic execution, plugin discovery, doctor
integration, candidate promotion, or a guarantee over deliberately detached
child processes.

## Phase 3 boundaries

Phase 3 versions newly written validation artifacts and centralizes policy for
fresh validation inside `apply`, CI, and worker flows. It does not add a
credential that lets a later process reuse `validation.json` as authorization;
that trusted transfer belongs with candidate promotion. `verified` remains
contextual evidence of an in-tree standard command, not a hermetic, signed, or
environment-reproducible attestation.
