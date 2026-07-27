# ADR-0011: Candidate promotion apply protocol

## Status

Accepted for issue #282 Phase 4.

## Decision

`patchforge apply` constructs a detached candidate from the run base commit,
validates that candidate using a policy captured from the unpatched base tree,
and atomically publishes only `refs/heads/patchforge/<run_id>[/issue]`.

The protocol is `candidate_promotion@1`. Before publication it writes a
`promotion_prepared` WAL containing the base ref and SHA, candidate ref and
SHA, receipt ref, and policy digest. One Git ref transaction verifies the base
and creates both candidate and receipt refs. Recovery accepts only a matching
candidate/receipt pair; partial or foreign states fail closed.

The validation subject includes repository common Git directory, base, patch
checksum, candidate commit, and policy digest. The policy is loaded from the
base tree, never from a candidate that may modify `orchestrator.json`.

CI and worker retain their legacy mechanisms temporarily. Every writer labels
its `apply.json` protocol and each reader rejects another protocol before it
mutates a repository.

## Consequences

The user's checkout and dirty state are not inputs to candidate applicability.
Historical unversioned apply WALs remain diagnostic evidence but are not
automatically resumed by the candidate protocol. Git hooks and configured
validator subprocesses remain inside the operator trust boundary; this ADR does
not claim hermetic execution or protection from malicious subprocesses.
