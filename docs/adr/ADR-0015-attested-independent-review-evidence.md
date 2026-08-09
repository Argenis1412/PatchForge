# ADR-0015: Attested independent review evidence

## Status

Accepted for issue #330. `review-evidence@2` is historical producer evidence and is not gate-recognized. Producers and consumer deploy as one `review-evidence@3` protocol boundary.

## Context

Local review commands are advisory: they cannot prove independent review or authorize a merge. Gate evidence originates in a base-branch-controlled workflow, is stored outside the pull-request tree, and is attested as exact immutable `review-record.json` bytes.

Two subjects remain necessary: plan review before implementation and diff review after it. This development-process contract does not change PatchForge product runs, public CLI behavior, `RunMetadata`, or product artifacts.

`review-evidence@2` did not make producer revision acceptance or Git-operation derivation sufficiently explicit for a deterministic consumer. A same-path workflow can evolve while retained records remain authentic, and Git rename detection is heuristic rather than a stored operation.

## Decision

### 1. Trusted boundary

The trusted workflow builds packets with harness code from the PR's trusted base revision. Plan declarations, diffs, branch names, commits, comments, and repository contents are untrusted data. The remote model receives only a canonical packet and has no checkout, shell, GitHub token, provider credential, write capability, or arbitrary tools.

The consumer also executes from the trusted base revision. It may fetch named untrusted commits as Git objects, but never checks them out or executes their contents.

### 2. Version and producer identity

Consumers dispatch on `schema_version` before interpreting status or subject. They reject `review-evidence@1`, `review-evidence@2`, unknown versions, and malformed records. New producers emit `review-evidence@3`.

An execution record has status `completed` or `unavailable`, an admitted phase-specific subject, and `model_tier`. `completed` contains findings; `unavailable` represents a provider or model failure after admission and contains one public redacted reason with no invented findings. An admission record has status `admission_rejected`, a candidate subject, one deterministic public reason, and no tier or findings. A successful job or uploaded artifact does not convert `unavailable` or `admission_rejected` to `completed`.

Attestation verification is an acceptance predicate, not a record field. For each record, the consumer verifies its downloaded bytes and requires expected repository and phase-specific signer path, plus verified `githubWorkflowSHA` and `sourceRepositoryDigest` both equal to the current PR `base_sha`. `workflow_name` is descriptive and never establishes trust. A producer or harness change has a different trusted base revision; prior evidence is stale.

The immutable discovered artifact ID is retained in the consumer receipt. Before a candidate is ordered, `artifact.workflow_run.id`, `record.workflow_run_id`, and the attestation's normalized invocation workflow-run ID must be equal, and `record_id` must equal `{workflow_run_id}:{phase}`. No value declared by the record can establish this binding.

### 3. Plan admission and plan subject

`.patchforge/review-plan.json` declares scope but never contains its own SHA. The harness admits it only when its commit is non-merge, has exactly current `base_sha` as parent, and changes only that declaration. It then adds trusted `plan_head_sha` to the canonical plan packet.

A v3 plan execution subject binds `base_sha`, `plan_head_sha`, and that packet digest. Changed base, merge, rebase, force-push, or non-linear post-plan history invalidates admission and requires a new plan review.

### 4. `canonical-change-set@1`

The deterministic mechanical input is a versioned canonical change set derived from the trees at `plan_head_sha` and `head_sha`, not from Git rename or copy heuristics. Each tree entry is exactly `(path, object_type, mode, object_id)`. Every path must be valid UTF-8; an invalid path fails closed. Entries are ordered by their UTF-8 path bytes.

- A path only in head is `add`; only in plan is `delete`; present in both with unequal entries is `modify`.
- A `rename` exists only for an exactly one-to-one removed/added pair with equal non-tree blob object ID and mode. An edited move remains `delete` plus `add`. A copy remains `add`. Duplicate candidate blob pairs remain independent add and delete operations.
- Gitlinks/submodules, blobs containing NUL, and blobs not valid UTF-8 are explicit non-text changes. They have no text line count and exceed the line budget fail-closed.
- A physical line is a maximal UTF-8 byte sequence terminated by `LF`, plus a final non-empty unterminated sequence. Adds count head lines, deletes count plan lines, and modifications count both. This is content accounting, not a diff-hunk algorithm.

The canonical change set serializes with the repository's canonical JSON function and includes its version, ordered entries, operations, tree metadata, and line counts. A canonical rename consumes one file-budget entry; every add, delete, and modify consumes one; an edited move or ambiguous pair consumes two. It is the sole input to `mechanical_scope_violation`.

### 5. Diff subject and review packet

`diff_review` binds `base_sha`, `plan_head_sha`, `head_sha`, and the digest of the v3 canonical diff-review packet. That packet contains `canonical-change-set@1` and, for each text change, deterministically ordered old/new UTF-8 contents. Both text fields are always present: a missing side is JSON `null`, while an empty file is `""`. Non-text changes contain explicit metadata only. Its digest binds model review input and mechanical evaluation to the same trees.

The phase is eligible only when `head_sha` differs from, and is a linear descendant of, `plan_head_sha`. The producer suppresses an empty post-plan chain.

### 6. Scope and evidence acceptance

`mechanical_scope_violation` may report only base mismatch, path outside the declared patterns, operation not allowed, changed-file budget exceeded, or changed-line budget exceeded. Rename evaluates both paths; the check never judges functional intent.

Artifact upload and attestation verification must both succeed before a record is evidence. An uploaded artifact whose attestation fails is artifact-present but evidence unavailable. Cancellation, runner failure, upload failure, unavailable verifier, or attestation failure fails closed.

The consumer enumerates every non-expired artifact whose phase, PR, and expected subject digest match. GitHub artifact ID is the immutable discovery identity; the published name is only a locator and may collide across retries. For every candidate it verifies artifact metadata, exact bytes, attestation, record version, phase, and subject. It selects the greatest `(emitted_at, workflow_run_id, record_id)` record; an equal ordering tuple with different bytes is ambiguous and fails closed.

Plan evidence is stale when base or plan SHA differs from the current PR. Diff evidence is stale when base, plan, or head SHA differs. Missing, expired, malformed, unverifiable, stale, mismatched, or ambiguous evidence fails closed. A diff record never substitutes for plan evidence.

### 6.1 Snapshot-bound advisory aggregation

The certified identity is `(pr_number, base_sha, plan_head_sha, head_sha)`. The
advisory consumer has exclusive concurrency by `pr_number`, not by snapshot,
so a new PR event queues behind the older evaluation. The workflow itself is defined
by the default branch, but it explicitly checks out and executes the consumer
and contracts at the certified `base_sha`; it never checks out or executes the
pull-request tree. It may fetch named untrusted Git objects for tree reading.

The consumer re-reads the live PR before publishing a terminal result. A
changed identity is `superseded`, produces a non-passing advisory check, and
certifies neither the old nor new snapshot. It waits for both producer records in 30-second intervals for at
most 15 minutes. At expiry it returns `evidence_incomplete` and fails closed.

When verified plan evidence exists for a plan-only snapshot (`head_sha == plan_head_sha`),
`pending_diff` is an observable internal wait state, not a terminal result or acceptance. A
later push queues a new evaluation; the older evaluation rereads the live PR, emits
`superseded`, and certifies neither snapshot before the queued evaluation starts. For a stable
snapshot, the only terminal consumer results are `accepted`, `triage_required`,
`blocking_pending`, `superseded`, and `evidence_incomplete`.

The v3 producer and consumer deployment is one protocol migration. The first
real v3 observation is a mandatory post-merge dogfooding PR; no decision gate
may be enabled until it records independently verified completed plan and diff
records for one snapshot.

### 7. Human decisions and findings

An attested override may satisfy only an attested `unavailable` record with the same subject and record ID, authorized actor, timestamp, rationale, accepted risk, and protected-environment approval. Missing evidence is never overridable. A completed plan or diff record with a low-confidence blocking finding produces terminal `triage_required`: it is neither acceptance nor an automatic block. A medium- or high-confidence blocking finding produces terminal `blocking_pending`; it cannot become `accepted` until a separately specified attested-resolution protocol binds a resolution to the same snapshot and finding. No such resolution protocol is recognized by v3. Advisory and informational findings do not automatically block.

### 8. Risk tiers

The canonical change set selects `high_assurance` for a protected path, more than two changed files, or more than 100 changed lines; otherwise it selects `economy`. Both tiers retain the same no-tools boundary.

## Consequences

- Verified immutable producer data is bound to the current PR base.
- Mechanical scope is reproducible from tree objects without environment-dependent rename/copy inference.
- v2 records remain historical observations, not credentials for the v3 gate.
- Existing product safety and public APIs remain unchanged.

## Follow-up verification

Before implementation, challenge and adversarially review the v3 criteria. Implementation must test version-first rejection, signer/base binding, every canonical change-set operation and ambiguity, binary/submodule/invalid-UTF-8 budget behavior, packet digest binding, stale/mismatched evidence, retry collisions, unavailable evidence, and hostile review input.

## Non-goals

- Changing the PatchForge product pipeline, public API, `RunMetadata`, or product artifacts.
- Adding provider secrets, branch protection, local hooks, or fork access to protected Environment credentials in this contract phase.
- Overrides and finding-resolution workflows remain separate work. The required design reviews approved the v3 producer/consumer implementation plan for issue #330.
