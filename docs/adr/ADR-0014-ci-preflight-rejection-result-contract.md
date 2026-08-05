# ADR-0014: CI preflight rejection result contract

## Status

Accepted for issue #313. Implementation is deliberately deferred to a
follow-up issue.

## Context

ADR-0013 requires CI to preflight the Architect segment before its first
pipeline event or durable run write. A rejected static provider preflight is
not a Scan or Plan failure: no stage has started, no run exists, and no run
evidence may be created.

`ci_result.json` is the machine-readable result consumed outside the
container. Its existing statuses describe successful CI or a failure of a
started stage. This record defines the separate public result contract needed
before CI can persist an initial preflight rejection.

## Decision

### 1. Versioned result and scope

The initial Architect preflight rejection uses `schema_version:
"ci_result@2"` and `status: "preflight_rejected"`. This status is valid only
in `ci_result@2`.

This ADR applies only to the Architect preflight before Scan and Plan. An
Executor preflight occurs after Plan has created a run and needs a separate
contract for its existing run evidence.

For `preflight_rejected`, the result has:

- `run_id: ""`;
- `branch: ""`;
- `affected_files: []`;
- `validation_passed: false`;
- `preflight_stage: "architect"`; and
- exactly one `preflight_reason` defined below.

The result file is an external CI output, not a run artifact, preflight event,
or authorization record. Its existence must not create or modify a run
directory, `run.json`, stage artifact, event log, or staging directory.

### 2. Result destination

The fully resolved `--result-file` path must be outside the fully resolved
target tree and outside every run or staging directory managed by the
workspace. This includes paths that reach a forbidden directory through a
symbolic link.

The result-writing guarantee applies only after this destination validation.
An invalid explicit result path is a command-invocation error: CI exits with
code `2`, writes no `CiResult`, creates no parent directory, and does not
redirect the output. This error occurs before the provider preflight contract.

For a valid destination, an initial preflight rejection writes exactly one
`ci_result@2` record there and exits with code `1`. It does not apply a patch,
push a branch, or create a pull request.

### 3. Deterministic public classification and redaction

The preflight implementation must use one classifier that accepts only the
credential-resolution result and the Architect provider-policy result. It
must apply this precedence:

| Condition | Later evaluation | `preflight_reason` |
| --- | --- | --- |
| Credential source is untrusted or unavailable | Do not evaluate policy | `credential_source_rejected` |
| Credential source is valid and a syntactically valid forced provider is not policy-admissible for Architect | Do not evaluate eligibility | `provider_policy_rejected` |
| Credential source and policy are valid, but no provider in the admissible chain is credential-eligible | None | `no_eligible_provider` |
| Credential source, policy, and eligibility are valid | Continue CI | No rejection result |

An unknown `--force-provider` remains CLI argument validation and is not a
preflight result. Mixed provider chains are classified only after the full
admissible chain is evaluated for eligibility.

`error` is a fixed operator-facing message derived from `preflight_reason`.
It must not serialize an internal exception or include credential values,
credential variable names, credential-file paths or contents, target paths,
or target-controlled content. The portable metadata fields already supported
by CI, including `issue_number`, `force_provider`, and `triggered_by`, may be
present when available.

### 4. Consumer compatibility

Results without `schema_version` are exclusively historical `ci_result@1`
records. Consumers must read results in two phases:

1. Decode the JSON object and inspect only `schema_version`.
2. Dispatch to the corresponding versioned schema, then validate and evaluate
   `status`.

A missing version dispatches only to v1. An unknown, null, malformed, or
otherwise unsupported version is rejected safely without examining `status`.
V2 retains the v1 fields and adds its version discriminator and preflight
fields. Consumers that authorize work only for `status == "applied"` remain
safe; strict consumers must support the v2 dispatch before CI emits v2.

Automation must present `preflight_rejected` as a pre-stage configuration or
policy rejection, not a Scan or Plan failure. It must not push a branch,
create a pull request, or treat the result as stage evidence.

## Consequences

- A static provider problem is observable to CI automation without creating a
  false stage failure or durable run evidence.
- An unsafe output location fails closed instead of changing the target or
  materializing run evidence.
- Future result consumers have an explicit compatibility boundary before
  interpreting a status.

## Follow-up verification

The implementation issue must verify:

- result-path rejection inside the target, runs, and staging without writes;
- one redacted result for each public preflight reason at a valid destination;
- classifier precedence for invalid credential sources, policy rejection, and
  chains without eligible credentials;
- v1 historical dispatch, v2 dispatch, and safe rejection of unknown
  versions; and
- absence of runs, events, staging, artifacts, pushes, and pull requests for
  an initial preflight rejection.

## Non-goals

- Changing `CiResult`, CLI argument handling, CI lifecycle, workflows,
  ProviderRuntime, provider policy, or SDKs.
- Redirecting an invalid result path.
- Defining the post-Plan Executor preflight result contract.
