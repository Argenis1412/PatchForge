# Experiment: Dogfooding 014 — attested review evidence producers

**Date:** 2026-08-09
**PR:** #337
**Intended base:** `68127c51608cfca43567e53cd2db853494f9576a`
**Plan commit:** `0b6043c145186cf53f0a3ad71234a95eefdc224d`

## Purpose

Observe the independently attested plan and diff evidence producers without
changing producer, consumer, or workflow code. This experiment does not test a
consumer gate and does not establish merge eligibility.

## Plan review observations

| Observation | Initial run | Rerun |
| --- | --- | --- |
| Run URL | `.../actions/runs/31298550204` | same run URL |
| Run identity | `31298550204`, attempt `1`, `pull_request_target` | `31298550204`, attempt `2` |
| Base / head | `68127c5` / `0b6043c` | same |
| Job conclusion | `success` | `success` |
| Record status | `unavailable(authentication)` | `unavailable(authentication)` |
| Record identity | `31298550204:plan_review` | same |
| Subject digest | `sha256:9d11f9c591a2f73d024f433d1104994d8a862c742835ab7537d7c42ab3ad3bb7` | same |
| Model tier | `economy` (workflow-configured) | `economy` (workflow-configured) |

Both attempts uploaded an artifact and passed GitHub attestation verification
against `Argenis1412/PatchForge/.github/workflows/review-plan.yml`. The exact
record subject matched the declared base, plan commit, and canonical packet.

The rerun published a different GitHub artifact ID and bytes while retaining
the same run ID, record ID, and canonical artifact name. Its publication is
therefore distinguishable only through GitHub attempt/artifact metadata, not
through the record identity contract.

Neither successful job represents a completed independent review: both
attested records report `unavailable(authentication)`. Artifact availability,
record parsing, subject matching, and signer verification were observed as
separate conditions.

## Diff review A

The next linear commit will trigger diff observation A for this report head.
After the following report commit, A will be stale because its `head_sha` and
diff digest bind this revision rather than the final PR head. Final diff B
evidence is recorded in the PR description to avoid creating another subject.
