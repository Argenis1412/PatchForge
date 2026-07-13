# ADR-0005: IssueContract — Canonical Source-Agnostic Issue Schema

**Status:** Accepted — 2026-07-13
**Deciders:** PatchForge core team
**Supersedes:** N/A

---

## Context

Today issues reach the pipeline through `IssueInput` (`src/orchestrator/schemas/issue.py`),
parsed from a human-written markdown file via `--issue-file`. `IssueInput` is
inherently markdown-shaped: it carries `raw` (the original file text) and its
fields are populated by a frontmatter parser (`parse_issue_markdown`).

Two more issue sources are anticipated:

1. A GitHub API adapter (future issue) that reads issues via the GitHub REST/GraphQL
   API rather than a local markdown file.
2. Scout (a separate product line, see `docs/planning/scout-vision.md`) that
   generates work items programmatically rather than from human-authored text.

Deciding the canonical, source-agnostic representation now — before either
consumer exists — avoids a retrofit once both are wired into the pipeline.
Retrofitting a shared shape after two independent consumers already exist
means reconciling their divergent assumptions under production constraints
instead of design-time ones.

Per Invariant #9 (meaning equals representation; provenance and meaning are
orthogonal — see `docs/context/CONTEXT.md`), the canonical shape must not
encode *where* a work item came from in the data itself. A `source` field (or
equivalent) would mean the same DTO shape carries different semantics
depending on which producer emitted it — the opposite of a canonical contract.

This ADR defines `IssueContract` — the schema only. No adapter, no Scout code,
no pipeline consumer wiring.

---

## Decision

### 1. Relationship to `IssueInput`

`IssueContract` **coexists** alongside `IssueInput` as a separate DTO.
`IssueInput` remains exactly as it is today: the markdown-parser output type,
consumed by `orchestrator.agents.architect` (`src/orchestrator/agents/architect/__init__.py:101`).
This issue does not touch `IssueInput`, `parse_issue_markdown`, or that
consumer.

`IssueContract` is not derived from `IssueInput` by inheritance or composition
in this issue. A markdown → `IssueContract` adapter is deferred to the issue
that wires the pipeline to consume `IssueContract` — at that point the adapter
decides how `IssueInput`'s markdown-specific fields (`raw`) map onto the
canonical shape.

**Rejected: subsume `IssueInput` into `IssueContract` now.** Doing so would
require touching the parser and the Architect consumer in this issue,
violating the "zero pipeline wiring" cut. It also forces a decision about
`raw`'s place in a canonical, multi-source schema before a second source
(GitHub API) actually exists to validate the shape against.

### 2. Versioning

`IssueContract` does **not** carry a `schema_version` field in this issue.

ADR-0004 scopes `schema_version` to `RunMetadata` under Invariant #3
(intermediate artifacts are consumed only within the run that produced them).
`IssueContract` does not currently cross that boundary either: nothing
persists an `IssueContract` instance outside of process memory in this issue,
since there is no adapter and no pipeline wiring.

Scout, once it exists, may persist `IssueContract` instances that are read by
a *later* version of PatchForge — a genuine cross-software-version scenario
that ADR-0004's rationale was written to prevent. That risk is real but
premature to solve here: versioning a schema with no persistence path yet is
speculative. See **Known Debt**.

### 3. Source-Neutrality (Invariant #9)

`IssueContract` achieves representational completeness across markdown,
GitHub API, and Scout **without encoding source in the DTO**:

- **No origin discriminator.** No field named `source`, `origin`, `producer`,
  `channel`, `feed_type`, `from_`, or any semantic equivalent.
- **No free-form escape hatch.** No `metadata: dict[str, Any]` or similar
  open-ended field. An open dict is a discriminator in disguise — nothing
  prevents a future producer from writing `metadata["source"] = "github"`,
  which reintroduces exactly the coupling this ADR forbids, just one layer
  removed from static analysis.
- **`extra="forbid"`.** `IssueContract.model_config = ConfigDict(extra="forbid")`.
  If a future adapter accidentally includes an origin-tagged field (or any
  unexpected key), validation fails loudly instead of Pydantic silently
  dropping the extra key and letting the violation pass undetected.

Optional fields are permitted only when the semantics is "this datum may not
exist for this work item" (e.g., a work item with no assignee). Optional
fields are not permitted when the semantics is "absent because it came from
source X" — that is source-encoding through omission, which is exactly what
this ADR prohibits regardless of whether it is expressed as a value or as a
gap.

### 4. Field Defaults

Semantic fields carry **no default value**. `IssueInput.title` defaults to
`"Untitled issue"` — appropriate for a markdown file where a human forgot the
frontmatter key, since the parser can reasonably guess. `IssueContract` has no
such excuse: a `title` default would let a broken future adapter (GitHub API
returning a malformed payload, Scout emitting an incomplete item) silently
produce a meaningless contract instead of failing at construction time, where
the actual defect is closest to its cause.

### 5. Shape

```python
class IssueContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    severity: Literal["low", "medium", "high"]
    labels: list[str] = []
```

- `title`, `description`, `severity` are required — no defaults, per Decision 4.
- `labels` defaults to `[]`: an empty label set is a legitimate, meaningful
  value (a work item with no labels), not a placeholder for a missing datum,
  so a default here does not violate Decision 4's rationale.
- `description` replaces `IssueInput.body`/`raw` — a single required text
  field is the smallest representation common to a markdown body, a GitHub
  issue body, and a Scout-generated description. Splitting `body` (parsed)
  from `raw` (original text) is markdown-specific; a canonical DTO does not
  carry a field whose entire purpose is "the pre-parse form of another
  field."

---

## Consequences

### Positive

- **No retrofit debt.** When the GitHub adapter and Scout integration land,
  they target an already-agreed shape instead of negotiating one under the
  pressure of a concrete integration deadline.
- **Provenance stays out of meaning.** `IssueContract` instances are
  interchangeable regardless of producer, satisfying Invariant #9. Consumers
  written against `IssueContract` never need a `match source:` branch.
- **Fail-loud posture.** `extra="forbid"` and the absence of defaults on
  semantic fields mean malformed input is rejected at the DTO boundary, not
  silently absorbed and discovered later downstream.

### Negative

- **Two issue DTOs exist in parallel** (`IssueInput`, `IssueContract`) with no
  automatic consistency check until the adapter issue lands. A future change
  to one's semantics (e.g., adding a `severity` value) can drift from the
  other unnoticed. This issue's test suite includes an equivalence check
  (`test_input_representable_as_contract`) specifically to bound this risk
  until the adapter exists.
- **Versioning deferred, not solved.** See Known Debt.

---

## Rejected Alternatives

**Subsume `IssueInput` now.** See Decision 1 — rejected because it forces
pipeline wiring changes in an issue explicitly scoped to exclude them, and
because the correct mapping of `raw` isn't yet informed by a second real
source.

**`source: Literal["markdown", "github", "scout"]` discriminator.** The
straightforward way to support multiple producers, and exactly what Invariant
#9 prohibits: it would make `IssueContract`'s meaning conditional on its
producer, defeating the purpose of a canonical contract.

**`metadata: dict[str, Any]` for source-specific extras.** Considered as a
pragmatic middle ground (let each source stash whatever it needs without
schema changes). Rejected because it is source-encoding without static
enforcement — nothing stops a producer from using it as a discriminator, and
`extra="forbid"` cannot police the contents of a field that is itself a
free-form dict.

**Add `schema_version` now.** Considered for symmetry with `RunMetadata` and
to preempt the cross-version risk described in Decision 2. Rejected because
nothing in this issue persists `IssueContract` outside process memory — adding
a compatibility mechanism for a persistence path that doesn't exist yet is
speculative and untested against a real use case.

---

## Known Debt

**Versioning is deferred until `IssueContract` acquires a persistence path
outside a single process/run.**

When Scout begins persisting `IssueContract` instances that are read back by a
different (later) PatchForge software version, the cross-version risk ADR-0004
was written to prevent becomes real for this schema too. At that point, either
amend this ADR or write a new one extending `schema_version` (or an equivalent
compatibility marker) to `IssueContract`. This is not a permanent decision —
it is a scope boundary valid until Scout's persistence model exists.

**Two parallel issue DTOs until the adapter issue lands.**

`IssueInput` and `IssueContract` are not mechanically kept in sync. The
round-trip/equivalence test added in this issue is a guardrail, not a
guarantee — it only tests the sample instances exercised by the test suite.
The adapter issue (markdown → `IssueContract`) is the point where this debt is
substantially closed for the markdown source.
