# ADR-0017: Audit signature signer authorization

## Status

Accepted for issue #239.

## Context

`verify-audit` previously established only that a signature was cryptographically
valid against the local GPG keyring. It did not identify whether the signer was
authorized for a particular verification.

## Decision

`verify-audit` accepts a repeatable, invocation-local `--trusted-fingerprint`
option. It neither reads nor writes persistent signer policy. Each supplied
fingerprint is a primary fingerprint of exactly 40 or 64 hexadecimal characters;
case is normalized to uppercase and duplicates are rejected.

Supplying an allowlist requires `manifest.json.asc`. GPG runs with `--batch` and
`--status-fd`; authorization succeeds only when all of the following hold:

- GPG exits with status zero.
- At least one well-formed `VALIDSIG` record is present.
- Every primary fingerprint in a `VALIDSIG` record belongs to the allowlist.

The `VALIDSIG` record is interpreted only through its documented primary
fingerprint field. Malformed records fail closed. Other status records, including
trust, expiry, and revocation notices, do not independently deny an otherwise
successful verification. This is historical signer authorization, not a policy of
current key eligibility. A nonzero GPG result always fails closed because status
output cannot prove that a partial transcript completed verification.

Without an allowlist, existing signature and `--require-signature` behavior is
preserved.

## Consequences

- A verifier can bind audit-bundle signatures to a locally chosen set of primary
  signers without changing bundle contents.
- AuditManifest v1 and v2 compatibility is unchanged.
- This decision does not change `pipeline.jsonl`, key distribution, or GPG keyring
  trust management.
