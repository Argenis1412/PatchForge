# ADR-0016: Audit bundle redaction profile

## Status

Accepted for issue #352.

## Context

`export-audit --redact` redacts selected `RunMetadata` fields. Per-run
`events.jsonl` is unstructured and can contain the same sensitive paths as the
workspace-wide `pipeline.jsonl` stream. Omitting it without a signed statement
would make a redacted bundle indistinguishable from an incomplete full export.

## Decision

New exports use `AuditManifest@2`. Its signed manifest declares:

- `export_profile`: `full` or `redacted`;
- `omitted_artifacts`: the complete list omitted by policy.

`full` requires an empty omission list. `redacted` requires exactly
`["events.jsonl"]`, and that artifact must not be declared or packaged. This
excludes arbitrary event payloads rather than claiming they can be safely
redacted.

The verifier dispatches explicitly by manifest version. It accepts v1 as the
fixed `legacy_full` historical contract, with no policy exclusions, and v2 only
when its profile and artifact set are coherent. Unknown versions fail closed.
New exports always emit v2. Old verifiers reject v2 rather than silently
misreading a redacted bundle as complete.

## Consequences

- A signed v2 manifest proves whether `events.jsonl` was intentionally omitted.
- Existing v1 bundles remain verifiable under their original contract.
- This decision does not redact global logs, mutate pipeline logs, or add GPG
  signer authorization; #239 owns signer trust.
