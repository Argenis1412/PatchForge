# ADR-0012: Executable plan authority boundary

## Status

Accepted — 2026-08-01

## Context

`execution_plan.json` is a deterministic executor contract containing complete
file mutations. Its structural validator proves safe paths, unique targets,
and source-content preconditions, but it does not prove that the artifact is a
refinement of the persisted `plan.json` for the current run.

The workspace is a persistence boundary. An artifact's presence therefore
cannot grant authority to enter `preview`, and a manually injected executable
plan must not bypass the Architect plan or its risk limits.

## Decision

No current pipeline consumer treats a persisted `execution_plan.json` as an
authorized transition input. `preview` and the work-queue Executor/Validator
stages reject the artifact with the stable `unbound_execution_plan` contract
violation before stage execution, staging, or event persistence.

The deterministic executor and its typed schema remain available as internal
mechanisms. They do not establish provenance or authorize a pipeline
transition by themselves.

## Future work boundary

A future issue may introduce an authorized compiler, but it must define and
verify a binding between the compiled artifact and the run's persisted
`plan.json`, `run.json`, base commit, task identifiers, declared paths, and
risk budgets. That design is separate from this rejection and may not be
introduced by restoring automatic artifact discovery.

## Consequences

- A stale, malformed, empty, directory, or symlink entry named
  `execution_plan.json` fails closed.
- Existing direct executor tests remain valid because the mechanical executor
  API is unchanged.
- The deterministic Dogfooding 013 injection remains historical evidence, not
  a supported public authorization path.
