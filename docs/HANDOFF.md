# Handoff: Reviewable Patch Workflow

## Product Direction

orchestrator-core is moving from a public “multi-agent runtime” framing toward a Git-native
reviewable patch workflow:

```text
Repository → Scan → Plan → Patch → Validation → Apply
```

The user-facing value is the patch, not the agent. Internal agents remain useful implementation
units, but product UX and documentation should lead with Scan, Plan, Preview, Apply, and Run.

## Current Internal Runtime Status

| Internal stage | Status | Product role |
|---|---:|---|
| Scout | ✅ Complete | Powers `scan` and findings generation. |
| Architect | ✅ Complete | Powers `plan` and task ordering. |
| Executor | ⏳ Pending redesign | Should evolve from applying changes to generating patch artifacts. |
| Validator | ✅ Complete | Powers validation reports for generated patches. |
| Reviewer | ⏳ Pending | Deferred until the core patch workflow is reliable. |

## Binding Product Rule

Before `apply`, there must be **zero target repository modifications**.

Allowed before `apply`:

- read repository files
- inspect Git metadata
- write orchestrator workspace artifacts
- generate `patch.diff`
- validate in a non-mutating way

Only `apply` may modify the target working tree, and it must do so through Git safety checks.

## Near-term Implementation Order

1. Keep docs aligned with [ADR-003](./adr/003-product-contract.md).
2. Add `doctor` for environment and repository readiness.
3. Split `plan` from `preview`.
4. Redesign artifacts around `workspace/runs/{run_id}/`.
5. Implement Git-safe `apply`.
6. Add risk budgets and change limits.

Deferred:

- monorepos
- TypeScript
- migration packs
- CI review
- autonomous bug investigation
