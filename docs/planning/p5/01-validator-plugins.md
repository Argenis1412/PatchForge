# P5 — 1. Validator Plugins

> **GitHub issue:** #282  
> **Status:** In progress — Phase 1

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

### Phase 3 — Versioned results and authorization decisions

- Version `validation.json`, distinguish verified V2 results from historical
  V1 records and centralize authorization in `ValidationDecision`.

### Phase 4 — Candidate promotion

- Validate an isolated candidate commit before publication and promote it with
  a Git compare-and-swap. No required validation occurs after publication.

### Phase 5 — Diagnostics and end-to-end integration

- Wire doctor to the shared process contract; document migration and run
  preview/apply/CI regression coverage.

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
