# ADR-0013: Provider preflight and operator credential boundary

## Status

Accepted for issue #302. Implementation is deliberately deferred to follow-up
issues.

## Context

PatchForge needs a provider contract that an external operator can understand
and trust before it runs an LLM stage. The current bootstrap searches several
`.env` locations, provider clients read process environment variables when
they are first created, and provider routing is distributed across the
Architect, Executor, and diagnostics. Those behaviors do not establish which
credentials are authorized to send target code to a provider.

This is also a pipeline-state concern. A missing credential is an operator
configuration problem, not a failed Plan or Preview attempt. Preflight must
therefore be able to reject an invocation before the stage creates durable
evidence or changes canonical run state.

## Decision

### 1. Operator credential sources

LLM provider credentials may originate only from one of these sources:

1. The inherited process environment.
2. An explicit `--env-file` supplied by the operator.

No command discovers or loads a `.env` file implicitly from the current
working directory, the target repository, or the installed package. An
explicit `--env-file` is invalid when its fully resolved path is inside the
fully resolved target tree. This includes a path that reaches the target
through a symbolic link. Explicitness is not evidence that a target-owned
secret is an operator credential.

When `--env-file` is absent, inherited provider credentials are the complete
credential source. When it is present, it is the complete credential source:
it replaces rather than supplements inherited provider credentials. Other
non-provider environment variables retain their normal process semantics.

The resolver creates one explicit credential context for a command invocation.
It records provider presence, never credential values, file contents, or
sensitive paths. LLM SDKs and clients receive credentials from that context;
they do not read `os.environ` themselves. LLM clients are invocation-scoped
and must not use module-global singleton state that can retain a prior
credential context.

Before every preflight and Validator process, PatchForge constructs a sanitized
child environment without provider variables, regardless of whether the
credential context came from inherited variables or `--env-file`. Target-
controlled commands, including Validators running against an isolated target
copy, must never inherit provider variables. The credential boundary protects
both provider selection and exposure to target subprocesses.

### 2. Availability vocabulary and shared policy

The following terms are distinct:

- **Credential eligibility**: the resolved context contains the credential
  required by a provider, and it is neither missing, blank, nor statically
  malformed according to that provider's local format rules.
- **Policy admissibility**: the provider is permitted for the stage, known
  task risk, and any force-provider request.
- **Operational availability**: an admissible provider can be called at that
  moment, including circuit-breaker state and runtime failures.

Doctor and preflight use the same credential-eligibility and
policy-admissibility functions. They do not claim operational availability and
do not invoke SDKs, make network calls, or probe provider accounts. A
diagnostic's "first eligible provider" means the first provider in the policy
chain that is credential-eligible and policy-admissible. Runtime applies its
operational checks afterward and records the provider actually used or the
operational failure. Circuit breakers are not a reason for Doctor to promise a
different provider.

Missing, blank, and statically malformed credentials are preflight failures:
they preserve `scanned` for Plan and `planned` for Preview. A provider-rejected,
expired, or revoked credential is an operational authentication failure because
it can only be discovered by a provider call. It is outside effect-free
preflight and follows the stage's ordinary runtime failure contract; it does
not retroactively turn the preflight into a state-changing operation.

The shared policy is consumed by Doctor, Architect, Executor, and any
LLM-backed Validator summary. It is the only authority for provider ordering,
credential requirements, and `--force-provider` validation.

### 3. Planning and task risk

Before Architect output exists, `plan` has no authoritative task risk. Its
preflight is therefore a planning-stage policy decision, not a speculative
high-risk decision. It may admit an eligible low/medium planning chain without
requiring Claude.

Architect output is the authoritative source of task `risk_level`. Before
Executor processes an already classified high-risk task, the policy requires
an eligible Claude credential. A high-risk task produced by a plan made with a
low/medium chain remains blocked until that condition is met. `--force-provider`
cannot bypass this requirement: for a high-risk task it must select Claude;
another provider is a policy rejection before any provider call.

Executor preflight evaluates this rule before its first event, staging write,
artifact write, or run-state mutation. Thus a high-risk task with a valid
non-Claude `--force-provider`, including `gemini`, is rejected without any of
those effects; Claude remains the only permitted forced provider for that task.

### 4. Effect-free preflight and retry semantics

Provider preflight only resolves credentials and evaluates the shared static
policy. It must not:

- instantiate or invoke an SDK;
- emit `stage_start`, failure, or provider events;
- create, remove, or clean staging;
- write plan, patch, validation, event, or other run artifacts; or
- mutate `run.json` or its canonical status.

Diagnostics may be printed to stdout. Persisting a preflight result requires a
separate ADR that defines its authority and redaction contract.

For local commands, a rejected Plan preflight preserves `scanned`, and a
rejected Preview preflight preserves `planned`. The same stage command is the
authority to retry: it reloads `run.json`, resolves a fresh credential context,
and runs preflight again after the operator corrects configuration. A missing
or invalid provider credential must not introduce `failed`.

`ci` preserves the same composition contract as local stages. It preflights
the Architect segment before its first pipeline event or durable write, then
preflights the Executor segment after task risk is known and before its first
event, staging operation, artifact write, or run-state mutation. A future CI
result output is not a preflight event and must be specified separately if it
records a rejected preflight.

## Consequences

- Operators must provide credentials outside the repository they ask
  PatchForge to analyze.
- A run can continue after correcting credentials without recovering from a
  false terminal state.
- A static diagnostic can explain allowed routes without asserting that a
  provider's account, network, or circuit breaker will permit a call.
- Implementing this decision requires at least a resolver/client change and a
  stage/CI lifecycle change. Those remain separate issues to keep each change
  bounded.

## Migration

The current multi-location `bootstrap_environment()` behavior is replaced only
in the credential-resolution implementation issue. That issue must introduce
the resolved context, remove implicit `.env` discovery for providers, make LLM
clients invocation-scoped, and scrub provider credentials from target child
environments. If those changes exceed its agreed budget, child-environment
scrubbing is a separate prerequisite issue before stage preflight is enabled
for external users.

That cutover includes Doctor. Its existing `check_api_keys()` path must
delegate credential eligibility and policy admissibility to the shared policy;
it must not retain separate environment-variable validation. The implementation
must verify that Doctor and runtime return the same static eligibility result
for the same resolved context.

The lifecycle implementation issue then adds the shared preflight to `plan`,
`preview`, and the equivalent `ci` boundaries. It must prove that a rejection
does not change canonical run state or stage artifacts.

## Rejected alternatives

### Treat every Plan as potentially high-risk

Rejected because task risk does not exist before Architect output. It would
reject low/medium planning work solely because of a hypothetical later task.

### Permit target-local env files when passed explicitly

Rejected because command-line explicitness does not prove ownership of a file
inside an untrusted target tree.

### Let each client read the environment at call time

Rejected because it creates multiple credential authorities and does not make
retry behavior demonstrable in a long-lived process.

### Report circuit-breaker state as credential availability

Rejected because it conflates static policy with runtime operational state and
makes Doctor promise a provider selection it cannot guarantee.

## Verification requirements for follow-up issues

- An env file inside the target is rejected without exposing its resolved path
  or contents.
- Inherited-only and explicit-env-file credential contexts have the specified
  replacement semantics.
- Two invocations with different credential contexts, while process environment
  variables change between them, use only their respective invocation-scoped
  credentials and retain neither prior credentials nor module-global client
  state.
- Doctor and runtime agree on static policy eligibility.
- A low/medium Plan can proceed with its eligible planning chain; a classified
  high-risk task cannot reach Executor without Claude.
- An invalid force provider and a missing forced-provider credential reject
  before SDK use.
- A high-risk task forced to Gemini rejects before provider calls, events,
  staging writes, artifacts, or run-state changes.
- Rejected Plan, Preview, and CI preflights leave the documented state and
  artifacts untouched.
- Validator processes cannot read provider credential variables from either
  inherited or explicit-env-file credential contexts.
