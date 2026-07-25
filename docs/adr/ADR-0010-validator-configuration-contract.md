# ADR-0010: Versioned validator configuration contract

## Status

Accepted for issue #282, phases 1-3.

## Decision

`orchestrator.json` without `schema_version` is the supported V1 legacy
profile. It accepts the existing known configuration fields and preserves their
behavior. Validator declarations require schema version `2.0` and are parsed
as one atomic document: malformed JSON, unsupported versions and unknown
fields are configuration errors.

Validator declarations are in-tree adapter configuration, not a dynamic plugin
mechanism. Their `id` is the sole unique key; repeated adapter types and argv
are intentional and ordered. `command` and `tox` roles are operator-declared
metadata for later validation policy checks, not proof of command semantics.

## Consequences

Existing versionless command override configurations remain valid. New V2
configurations fail early rather than silently falling back to defaults. A
shared process-preparation representation is the only permitted foundation for
future adapter execution and doctor command diagnostics.

## Phase 2 execution-result contract

Phase 2 extends the existing validation result models compatibly so V2
executions can be audited before `validation.json` receives a formal schema
version in Phase 3. Results written by V2 execution carry
`result_profile: "v2"`; historical records with no profile are explicitly
read as pre-discriminator V1 records. This is a read-compatibility rule, not a
heuristic or migration.

Each V2 result is identified by the configured validator `id`, its adapter,
and its declaration order. The aggregator, rather than a legacy runner, is
the sole authority for evaluating declared success codes. Legacy runners and
their historical `(0, 5)` pass rule remain unchanged.

V2 records terminal states only. A non-approved terminal state stops the
ordered sequence and records later declarations as `not_run`. Coverage is
`verified` only for a fixed adapter's standard command; `command`, `tox`, and
all command overrides are `declared_only`. Unavailable or incomplete execution
is not approval: it produces an incomplete, non-authorizable global result.

Validator processes are supervised as a managed process tree. A timeout is
persisted only after cleanup is confirmed; cleanup that cannot be confirmed is
recorded as `cleanup_failed` and is non-authorizable. This does not provide a
hermetic sandbox or control intentionally detached processes.

## Phase 3 artifact and authorization contract

New `validation.json` writers emit additive artifact schema version `2` with
an explicit authorization profile, canonical validation requirements, a
validation subject, and a derived decision. Artifact schema and authorization
policy version independently: supported profiles are `legacy_v1_compat@1` and
`v2_verified_roles@1`; unknown versions or profiles are non-authorizable.

`legacy_v1_compat@1` is selected only for new executions of a versionless V1
configuration and preserves its `overall_passed` behavior during the
transition. V2 configuration selects `v2_verified_roles@1`, which authorizes
only an approved result whose canonical required roles all have `verified`
coverage. `command`, `tox`, and command overrides remain `declared_only` and
cannot satisfy that profile.

Historical unversioned artifacts remain readable for diagnosis but are never
authorization credentials. Persisted decisions are audit evidence only: a
separate `apply` run or worker must validate within its own controlled flow
before it confirms a mutation. The persisted artifact does not transfer
authority across process boundaries. `verified` records contextual execution
of a fixed in-tree standard command; it is not a hermetic or signed
attestation of a toolchain or environment.
