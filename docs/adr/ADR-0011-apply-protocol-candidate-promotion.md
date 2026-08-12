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

Base compatibility is evaluated at two distinct protocol boundaries. Before a
new candidate is constructed or either promotion ref is published, the live
base branch must still equal the run's recorded base commit. After the atomic
transaction publishes a matching candidate/receipt pair, recovery may finish
the run even if that live base later advances. That recovery is authorized only
when the WAL, policy, validation output, and validation subject bind the
recorded candidate identity; corrupt, partial, foreign, mismatched, or
unauthorized state fails closed.
Recovery reads only that persisted evidence, the common Git identity, and the
published refs. It does not reload configuration or credentials from an
advanced base, because those mutable inputs cannot invalidate an already
published and authorized promotion.

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
