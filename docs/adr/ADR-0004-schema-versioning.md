# ADR-0004: Schema Versioning Policy

**Status:** Accepted — June 2026
**Deciders:** PatchForge core team
**Supersedes:** N/A

---

## Context

PatchForge produces structured artifacts at each pipeline stage (`run.json`,
`plan.json`, `patch.diff`, `validation.json`). Under Invariant #3, the
source-of-truth guarantee is scoped to a single pipeline run: all artifacts are
consumed within the run that produced them, and the schema is consistent
throughout.

At P2 (dogfooding), this single-run restriction expires by construction.
Experiment artifacts — `run.json` files produced by one software version — will
be compared, reviewed, and potentially reprocessed by a later version of the
software. Cross-version artifact loading becomes a system requirement, not a
theoretical edge case.

Without an explicit versioning policy, two failure modes are possible:

1. **Silent corruption:** Pydantic loads an incompatible artifact, silently
   inferring defaults for missing fields or ignoring extra fields. The pipeline
   proceeds on stale data.
2. **Opaque failure:** Pydantic raises a `ValidationError` with no indication of
   whether the cause is agent error, persistence corruption, or schema evolution.

ADR-0004 establishes the schema versioning policy that makes mismatches
detectable, attributable, and fail-fast — before P2 begins.

---

## Decision

### 1. Format

The version field is a **monotonic integer**: `schema_version: int`.

Initial value: `1`.

Not semver — three-component versions are overkill for a single internal scalar
representing a compatibility boundary. Not date-based — a date does not encode
compatibility; two artifacts produced on the same date by different software
versions may be incompatible, while two produced years apart may be compatible.

### 2. Scope

`schema_version` is carried by **`RunMetadata` only**.

Stage-intermediate schemas (`ArchitectOutput`, `Plan`, `ExecutorOutput`,
`ValidatorOutput`) are out of scope. Under Invariant #3's single-run
restriction, intermediate artifacts are never consumed outside the run that
produced them. Versioning them before they become persistent across execution
boundaries would be premature.

This restriction is known to expire at P3. See **Known Debt**.

### 3. Breaking Change Definition

A **breaking change** is any schema modification that makes an artifact produced
under version N unloadable or semantically incorrect when loaded under version
N+1.

| Change type | Breaking? | Version action |
|-------------|-----------|----------------|
| Field removal | **Yes** | Increment `schema_version` |
| Field rename | **Yes** | Increment `schema_version` |
| Field addition with default | **No** | No increment |
| Type narrowing on existing field | **Yes** | Increment `schema_version` |

**Concrete breaking example:** Removing `software_version: str` from
`RunMetadata`. An artifact produced under version 1 that contains
`"software_version": "0.4.0"` cannot be correctly interpreted under a schema
that no longer includes this field — the information is silently dropped on load.

**Concrete additive example:** Adding `tags: list[str] = []` to `RunMetadata`.
An artifact produced under version 1 that does not contain `"tags"` loads
correctly under the new schema with `tags=[]` inferred from the default. No
compatibility boundary is crossed.

### 4. Increment Trigger

The `CURRENT_SCHEMA_VERSION` constant bump **must live in the same commit** that
introduces the breaking schema change. Enforced by code review, not CI.

Rationale: version and schema change are atomically coupled by semantics. If
they are separated into distinct commits, a window exists between commit 1
(schema change) and commit 2 (version bump) where the software loads artifacts
under the wrong version. Any artifact produced in this window will be
misclassified as compatible. There is no CI mechanism that can detect this gap
without knowing the intended version ahead of time.

Code review is the correct enforcement point: the reviewer confirms that any PR
introducing a breaking schema change also increments `CURRENT_SCHEMA_VERSION` in
the same diff.

### 5. Mismatch Behavior

When `pipeline.py` loads a `RunMetadata` artifact and finds
`loaded.schema_version != CURRENT_SCHEMA_VERSION`, it raises `SchemaVersionError`
immediately.

```python
raise SchemaVersionError(found=loaded.schema_version, expected=CURRENT_SCHEMA_VERSION)
```

Properties:

- **Typed exception** — `SchemaVersionError` with `found: int` and
  `expected: int` attributes
- **No warning** — the pipeline does not proceed with a stale artifact
- **No migration** — the policy defines no recovery path; the caller decides
  how to handle the mismatch
- **No silent load** — there is no fallback to loading under a mismatched schema

---

## Consequences

### Positive

- **Early detection:** Schema mismatches are caught at load time, before any
  stage transition proceeds on stale data.
- **Attributable failures:** A `SchemaVersionError` unambiguously signals schema
  evolution, not agent error or persistence corruption. This closes the
  diagnostic gap identified in Invariant #3's cross-schema case.
- **Audit integrity:** Experiment artifacts in P2 dogfooding carry an explicit
  compatibility marker. Reprocessing a `run.json` from an older software version
  will fail loudly rather than silently misinterpret the data.

### Negative

- **Migration burden:** Each breaking change requires a decision about existing
  artifacts. This policy defines no migration path — callers must discard or
  manually convert incompatible artifacts. As breaking changes accumulate,
  experiment archives may become partially inaccessible.
- **Enforcement gap:** Code review is a social control, not a technical one. A
  reviewer may miss the version bump on a breaking change. This gap is accepted
  as proportionate to the current team size and artifact volume.

---

## Rejected Alternatives

**Semver (`1.2.3`):** Three-component versioning encodes major/minor/patch
semantics meaningful for public APIs. For an internal artifact with a single
compatibility boundary (breaking vs. additive), a three-component version adds
complexity without information. `schema_version: int = 1` expresses the same
semantics with lower cognitive overhead.

**Date-based (`2026-06-11`):** A date expresses when an artifact was produced,
not whether it is compatible with the current schema. Two artifacts produced on
the same date by different software versions may be incompatible; two produced
a year apart may be compatible. The date does not answer the question "can the
current code load this artifact?"

**No versioning:** Without explicit versioning, schema mismatches produce either
silent corruption (Pydantic infers wrong defaults) or an opaque `ValidationError`
indistinguishable from agent error. Neither is acceptable for an auditable
pipeline.

---

## Known Debt

**"RunMetadata only" restriction expires at P3.**

When async workers (P3) begin operating on separate machines, intermediate stage
schemas (`ArchitectOutput`, `Plan`, `ExecutorOutput`, `ValidatorOutput`) will be
serialized and transmitted across worker boundaries. At that point they acquire
cross-version lifespans and must carry `schema_version`.

Extending this policy to intermediate schemas is deferred until P3 begins. The
appropriate action at that point is to amend this ADR or create ADR-0005 defining
versioning policy for intermediate schemas.

This debt is acknowledged. The "RunMetadata only" restriction is not a permanent
architectural decision — it is a scope boundary valid for V2 that expires at P3.
