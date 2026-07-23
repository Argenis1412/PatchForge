# ADR-0010: Versioned validator configuration contract

## Status

Accepted for issue #282, phase 1.

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
