# ADR-0015: Attested independent review evidence

## Status

Accepted for issue #328. Implementation is deliberately deferred to follow-up
issues.

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

### 2. `review-evidence@1` evidence set

`review-evidence@1` defines an evidence set of separate immutable records for
the `plan_review` and `diff_review` phases. The plan-review workflow emits the
plan record before implementation; the diff-review workflow emits the diff
record after implementation. CI composes the selected records by phase and
subject without modifying either artifact. Each record has:

- `record_id`, `phase`, and `status` (`completed` or `unavailable`);
- the emitting workflow name, workflow run identifier, timestamp, and
  attestation reference;
- `model_tier` (`high_assurance` or `economy`); and
- its phase-specific canonical subject.

A `plan_review` subject contains `base_sha` and the digest of one canonical
plan-scope packet. That packet contains the same `base_sha`, allowed path
patterns, allowed Git operations (`add`, `modify`, `delete`, `rename`), and
maximum changed-file and changed-line budgets.

A `diff_review` subject contains `base_sha`, `head_sha`, and the digest of the
canonical actual diff. A record's subject is immutable: a record for one plan
or head cannot satisfy another.

`completed` records contain a finding list. Every finding has a stable id,
evidence reference, `severity` (`blocking`, `advisory`, or `informational`),
and `confidence` (`high`, `medium`, or `low`). `unavailable` records contain
one redacted public reason code and no invented findings.

### 3. Mechanical scope is intentionally narrow

The deterministic comparison between plan and diff is named
`mechanical_scope_violation`. It may report only these conditions:

- actual base SHA differs from the plan packet base SHA;
- a changed path is outside the allowed patterns;
- a Git operation is not allowed for that path; or
- the changed-file or changed-line budget is exceeded.

For a rename, both the source and destination path must be allowed. The check
does not decide whether a behavior implements the plan. Functional
contradictions remain diff-review findings, subject to human assessment rather
than a deterministic scope decision.

### 4. Phase completion, availability, and human decisions

Each review workflow invocation emits exactly one attested phase record for its
subject. CI selects one matching record for each required phase. A `completed`
record satisfies its phase, subject to unresolved blocking findings. An
`unavailable` record does not satisfy it alone. A retry emits a new record; it
does not rewrite the earlier record.

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
The diff gate evaluates the actual diff after implementation. A protected path,
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
- Automated checks can enforce mechanical boundaries while leaving semantic
  disagreement to a reviewer and documented human decision.
- Follow-up implementation needs repository configuration for CI credentials,
  artifact attestation, authorized override actors, and protected environments.

## Follow-up verification

The implementation issue must verify:

- plan and diff records bind only their respective canonical subjects;
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
