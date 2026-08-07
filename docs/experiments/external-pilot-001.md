# Experiment: External Pilot 001 — Accompanied low-risk workflow evaluation

**Status:** Planned — do not run until a participant and target repository are
nominated.

**Objective:** Observe one external user complete the bounded PatchForge
workflow on a real, non-sensitive Python repository. The evidence will decide
the next product investment; it is not evidence that the product is ready for
broader rollout.

## Eligibility

- The participant owns or is authorized to use the target repository and gives
  written consent to the observation and sanitized evidence capture.
- The repository is a small Python project with existing automated tests and
  contains no regulated, production, customer, or otherwise sensitive data.
- The task is a low-risk correction scoped to existing files. It must not add
  secrets, change authentication or payments, alter production infrastructure,
  or require a broad refactor.
- The operator keeps any credential file outside the target repository and
  uses a workspace outside the target tree.

## Safety Boundary

The pilot permits only this sequence:

```text
doctor -> scan -> plan -> preview
```

`apply` is outside the pilot. PatchForge must not invoke it, and no participant
is asked to invoke it as part of evidence collection. The participant may make
an independent human decision about a reviewed patch after the pilot; that
decision is not a PatchForge action and must not be recorded with sensitive
repository details.

Stop immediately if any command attempts to modify the target before `apply`,
credentials could be exposed, the task exceeds the agreed low-risk scope, or
the participant withdraws consent. Do not collect further evidence after a
stop condition.

On consent withdrawal, delete previously captured participant-linked
observations. Retain only the minimal non-identifying abandonment metadata
needed for the project ledger: `outcome: abandoned`,
`reason: consent_withdrawn`, and the sanitized stages reached or not reached.

For a suspected credential exposure, notify the credential owner and revoke or
rotate the credential. Create only a sanitized incident record with the
`suspected_credential_exposure` category. Do not resume the task until
containment is complete and the credential owner explicitly authorizes a new
attempt.

## Evidence Handling

Do not publish or attach:

- API keys, credential names, environment-file paths, or environment contents;
- private repository URLs, absolute target or workspace paths, source code, or
  unsanitized PatchForge artifacts;
- customer, personal, production, security-sensitive, or proprietary data.

Record only sanitized observations. Replace the repository identity with a
generic description, describe provider configuration without secrets, and use
short redacted error categories rather than raw output.

Use these normalized run-record values:

- **Participant task and expected outcome:** one concise, redacted description
  in the form `<task category>; expected: <observable result category>`.
  Do not copy instructions, source code, identifiers, or repository data.
- **Commands and stage exit outcomes:** one entry per stage in the form
  `stage=<doctor|scan|plan|preview>; status=<succeeded|failed|abandoned|not_reached>; exit=<integer|not_run>; reason=<normalized_category|none>`.
  Do not record raw commands, arguments, paths, URLs, identifiers, or output.

## Run Record

Complete this section only after the participant consents and the run finishes
or is abandoned. Every field must contain `Pending` before the pilot, a
normalized value after completion, or an explicit normalized abandonment value;
do not leave fields blank.

| Field | Sanitized record |
| --- | --- |
| Date and PatchForge revision | Pending |
| Run outcome (`completed`, `abandoned`, or `not_started`) | Pending |
| Abandonment reason (`none` for completed runs) | Pending |
| Stop condition triggered (`none` if no stop condition triggered) | Pending |
| Stages not reached (ordered stage names or `none`) | Pending |
| Repository characteristics | Pending |
| Participant task and expected outcome | Pending |
| Commands and stage exit outcomes | Pending |
| Provider configuration category | Pending |
| Observed friction and recovery attempted | Pending |
| Plan/preview result and participant assessment | Pending |
| Consent confirmation | Pending |
| Artifacts deliberately excluded | Pending |

## Decision Rule

After the run, review the sanitized record against the current backlog. Open a
new, scoped issue only for a reproducible problem that materially blocked the
participant or reduced confidence in the safety contract. Do not start P5,
Executor CI preflight, broad refactors, or coverage campaigns solely from this
single observation.
