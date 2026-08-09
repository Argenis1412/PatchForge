# ADR-0015: Attested independent review evidence

## Status

Accepted for issue #328. Evidence producers are delivered first; gate
enforcement and human-decision consumption remain follow-up work.

## Context

PatchForge's maintainer workflow uses Issue Clarifier, AC Challenger,
Adversarial Reviewer, and Diff Reviewer prompts. The current slash commands
are useful local discipline, but they run in the caller's conversation by
default. Their output cannot prove independent review, cannot safely decide a
merge, and is not reproducible evidence for a third party.

Independent review needs two distinct subjects. Plan review happens before an
implementation diff exists; diff review happens after implementation. A single
in-repository `review.json` cannot prove both phases without overwriting one
record, and adding it to a reviewed commit creates a self-referential hash.

This is a development-process contract. It must not change PatchForge product
runs, `RunMetadata`, public CLI behavior, or product artifact authorization.

## Decision

### 1. Trusted boundary and external evidence

Gate-recognized evidence is emitted only by a base-branch-controlled CI
workflow and stored outside the pull-request tree as an attested external
artifact. Local Claude Code agents and checked-in prompt templates may assist
reviewers, but their output is advisory and never satisfies a gate.

The trusted workflow must build its packet with harness code from the trusted
base branch. Plan text, diffs, branch names, commit messages, comments, and
repository contents are untrusted data. A model reviewer receives only the
canonical packet; it has no repository checkout, shell, GitHub token, provider
credential, write capability, or arbitrary tool access.

### 2. Versioned evidence sets

`review-evidence@1` remains closed and historical: consumers must reject an
unknown version before interpreting its status or subject. New producers emit
`review-evidence@2`, which defines separate immutable records for the
`plan_review` and `diff_review` phases.

An execution record has `status` `completed` or `unavailable`, a
phase-specific admitted subject, and `model_tier` (`high_assurance` or
`economy`). An admission record has `status` `admission_rejected`, a
phase-specific candidate subject, one public deterministic reason code, and
neither `model_tier` nor findings. `unavailable` represents only a
provider/model failure after admission.

A GitHub artifact attestation signs the exact immutable `review-record.json`
bytes.  An evidence record does not contain an attestation reference for
itself: consumers derive provenance by verifying those bytes against the
repository and a fixed signer workflow.  The record digest is an external
locator, never a mutable field added after attestation.

A plan execution subject contains `base_sha`, `plan_head_sha`, and the digest
of one canonical plan-scope packet. That packet contains the same `base_sha`,
allowed path patterns, allowed Git operations (`add`, `modify`, `delete`,
`rename`), and maximum changed-file and changed-line budgets.

A `diff_review` subject contains `base_sha`, `plan_head_sha`, `head_sha`, and
the digest of the canonical actual diff from `plan_head_sha` to `head_sha`.
A record's subject is immutable: a record for one plan or head cannot satisfy
another. An admission subject identifies the candidate base and head and uses
exactly one declaration provenance: a validated canonical declaration digest,
a raw candidate declaration digest, or an explicit absent/unreadable marker.
It never asserts a digest for bytes that do not exist or could not be read.

### 2.1 Plan admission is a linear Git transition

`.patchforge/review-plan.json` stores a plan-scope declaration, not its own
commit SHA. The base-branch-controlled harness reads its candidate bytes,
verifies that the candidate head is a non-merge commit whose sole parent is
the trusted `base_sha`, and verifies that it modifies exactly that plan file.
Only then does the harness add the trusted `plan_head_sha` and construct the
canonical plan-scope packet. This proves the complete admitted pre-
implementation transition without requiring a commit to contain its own OID.

Every commit from `plan_head_sha` through the implementation `head_sha` must
have one parent and form one linear chain beginning at `plan_head_sha`.  A
merge, rebase, force-push, or current pull-request base SHA that differs from
the attested `base_sha` invalidates admission and requires a new plan commit
and `plan_review`.  The gate evaluates the implementation delta only from
`plan_head_sha` to `head_sha`; it never folds later `main` changes into that
delta.

`completed` records contain a finding list. Every finding has a stable id,
evidence reference, `severity` (`blocking`, `advisory`, or `informational`),
and `confidence` (`high`, `medium`, or `low`). `unavailable` records contain
one redacted public reason code and no invented findings.

### 3. Mechanical scope is intentionally narrow

The deterministic comparison between plan and diff is named
`mechanical_scope_violation`. It may report only these conditions:

- current pull-request base SHA differs from the plan packet base SHA;
- a changed path is outside the allowed patterns;
- a Git operation is not allowed for that path; or
- the changed-file or changed-line budget is exceeded.

For a rename, both the source and destination path must be allowed. The check
does not decide whether a behavior implements the plan. Functional
contradictions remain diff-review findings, subject to human assessment rather
than a deterministic scope decision.

### 4. Publication, phase eligibility, and human decisions

Once the trusted harness runs, every deterministic admission outcome is
materialized as an execution or admission record. It becomes evidence only
when its artifact upload and attestation both succeed. A cancellation, runner
failure, upload failure, or attestation failure leaves evidence absent and
fail-closed; it must not be represented as an attested rejection.

The diff phase is eligible only when `head_sha` is a linear descendant of
`plan_head_sha` with at least one intervening implementation commit. The
workflow suppresses the diff job for `head_sha == plan_head_sha`; this is not
an admission rejection because no implementation candidate exists.

CI discovers candidate artifacts by the stable
`phase/PR/subject-digest/workflow-run-id` identity, downloads every
non-expired candidate, verifies its attestation against the fixed signer
workflow, and selects the greatest `(emitted_at, workflow_run_id, record_id)`
tuple among records for the same subject. A retry preserves the subject digest
and publishes a new workflow-run identity. An absent, expired, malformed, or
unverifiable artifact is missing evidence and is never an override candidate.

An attested human override may satisfy only an attested `unavailable` record.
It must reference that record id and the identical subject, identify the
authorized actor, timestamp, rationale, accepted risk, and protected-environment
approval reference. A missing phase record is never overridable; the workflow
must be re-run until it emits `completed` or `unavailable`.

Medium- or high-confidence `blocking` findings require an attested human
resolution tied to the finding and subject. Low-confidence blocking findings
require human triage but do not automatically block. Advisory and informational
findings never block automatically.

### 5. Risk tiers and timing

The plan gate evaluates the canonical plan-scope packet before implementation.
The diff gate evaluates the actual diff only after implementation. A protected path,
more than two changed files, or more than 100 changed lines selects the
`high_assurance` tier; other changes select the `economy` tier. Both tiers use
the same no-tools isolation boundary; they differ only in the configured model
cost tier.

If the actual diff produces `mechanical_scope_violation`, implementation must
return to plan review. CI must not infer a retroactive semantic plan review
from the diff.

## Consequences

- Review evidence is auditable without modifying the reviewed pull-request
  tree or certifying its own commit.
- A provider or model failure is represented honestly as `unavailable`, not as
  an empty successful review.
- Deterministic admission rejection is externally distinguishable from absent
  evidence only after successful artifact publication and attestation.
- Automated checks can enforce mechanical boundaries while leaving semantic
  disagreement to a reviewer and documented human decision.
- Follow-up implementation needs repository configuration for CI credentials,
  artifact attestation, authorized override actors, and protected environments.

## Follow-up verification

The enforcement follow-up must verify:

- plan and diff records bind only their respective canonical subjects;
- version-first rejection of unsupported evidence schemas;
- declaration provenance for valid, raw, absent, and unreadable candidates;
- suppression of diff review before an implementation commit; and
- all mechanical-scope conditions, including both sides of a rename;
- rejection of subject mismatches, absent records, malformed enum values, and
  overrides that do not reference an attested unavailable record;
- blocking-finding resolution and low-confidence triage behavior; and
- base-branch harness use and no-tools handling of hostile review input.

## Non-goals

- Adding Claude agents, GitHub Actions workflows, provider secrets, CI gates,
  hooks, or runtime validators.
- Changing PatchForge product pipeline behavior, public APIs, schemas,
  `RunMetadata`, or product run artifacts.
- Treating advisory local review output as trusted or merge-authorizing.
