# Adversarial Audit — Phase 2 Planning Session

> **Date:** 2026-06-10
> **Session:** 27 adversarial attacks on Phase 2 architecture (P0 blockers and invariants)
> **Role format:** Attacker → Defender → Curator (plan updater)
> **Outcome:** Architecture validated as implementation-ready. 5 planning documents updated.
> **Final tally:** 27 attacks, 23 approved, 4 rejected. Session expired — no remaining attack vectors with >50% probability of forcing structural change.
> **Product Thesis V2** created as `docs/product-thesis-v2.md` — product definition distilled from adversarial process.

---

## Executive Summary

The adversarial session subjected PatchForge's Phase 2 architecture to 20 structured attacks targeting the Structured Contract Parsing (Issue A / T-03) design and the core architectural invariants.

**Results:**

| Metric | Value |
|---|---|
| Attacks launched | 27 |
| Defenses rejected | 4 (Attack 2: prompt text test; Attack 15: formal complexity claim; Attack 25: persist() is domain logic; Attack 26: parser position vs DTO semantics) |
| Invariants clarified | 5 (#1 orchestration scope, #2 round-trip addendum, #3 persistence semantics + behavioral consequences + temporal separation warrant, #9 scope) |
| New invariants created | 1 (#9 — inter-stage schemas are pure DTOs) |
| ADRs deferred | 3 (schema version identity, runtime roundtrip, diagnostic attribution) |
| Documents updated | `issue-a-design.md`, `roadmap-phase2.md`, `dogfooding-vision.md`, `CONTEXT.md` (root + PatchForge), `roadmap-phase2.md` (ADR-01 entry condition) |

**Architectural verdict (post-#23):** The system is ready for implementation of Issue A subject to closing one open precondition: ADR-01 (schema versioning policy) must be resolved before P2 begins. This is the strongest attack so far — it surfaces a load-bearing assumption (single-run scope) that will expire at P2. Unlike previous clarifications, this requires a scheduling change in the roadmap, not just a semantic clarification. The architecture remains internally consistent but now has a dated expiration on its scope restrictions.

---

## Attack Map

### Issue A — Structured Contract Parsing (T-03)

| # | Target | Verdict | Consequence |
|---|---|---|---|
| 1 | First JSON vs contractual object | Approved | Prompt contract reinforced (later retracted in #2) |
| 2 | Prompt text test | **Rejected** | AC6 reverted, AC8.1 removed — prompt text is not parser scope |
| 3 | `[]` vs `42` exception classification | Approved | Boundary redefined: opening token `{` → SchemaValidationError; anything else → LLMParseError |
| 4 | Brace-counting ambiguity | Approved | `raw_decode()` prescribed as canonical mechanism; AC2(f) added |
| 5 | Incidental `{file_path}` placeholders | Approved | Iterative `find('{')` + `raw_decode()` loop; AC2(g) added |
| 6 | Auxiliary dict before contractual object | Approved | AC2(h) documents intentional SchemaValidationError behavior |
| 7 | `risk.py` out of scope | Approved | Canonicity invariant documented in module docstring; AC10 added |
| 8 | Provisional exception hierarchy | Approved | Backward-compatible evolution documented; T-07 migration is additive |
| 9 | "Will not change" overpromise | Approved | Replaced with "backward-compatible evolution"; exception chaining mandated (AC11) |
| 10 | AC5 guard ambiguity | Approved | Precise predicate: `not (isinstance(schema, type) and issubclass(schema, BaseModel))` |
| 10bis | `BaseModel` itself rejected | Approved | `schema is not BaseModel` removed — parser does not judge caller intent |
| 11 | Dict-only canonicity contradiction | Approved | Canonicity lowered from function-level to module-level in AC10 |
| 12 | Dict-only vs array-shaped future contracts | Approved | Scope note: dict-only is Issue A scope; array support requires separate issue |
| 13 | Array as "additive extension" | Approved | Corrected to "deliberate semantic modification" in AC3 scope note |
| 14 | Object identity as conformance proxy | Approved | Replaced by mutation test with sentinel |
| 15 | Mutation test → Model A vs Model B | Approved | Mutation test withdrawn; conformance is structural (code inspection + call-sequence test) |
| 16 | "First JSON object" definition ambiguity | Approved | Definition re-anchored on RFC 8259 grammar, not `raw_decode()` |
| 17 | raw_decode-based vs RFC 8259-based definition | Approved | Semantic contract rewritten in terms of RFC 8259 |
| 18 | Positional extractor vs document parser | Approved | Positional extraction model declared explicitly in Section 2 |

### Architecture Invariants

| # | Target | Verdict | Consequence |
|---|---|---|---|
| 19 | Invariant #3 ambiguity (Model A vs B) | Approved | Invariant #3 clarified: disk is transactional boundary, not audit side-effect |
| 20 | Temporal separation as contract boundary | Approved | Contract vs Persistence boundary documented |
| 21 | Invariant #9 "meaning" subdetermination | Approved | Scope clarified: representational completeness ≠ execution determinism |
| 22 | Invariant #3 invariant-vs-convention status | Approved | Behavioral consequences documented: resumability + distributed correctness |
| 23 | Invariant #2+3 composition vs schema evolution policy | Approved | ADR-01 promoted from empirical precondition to P2 entry condition |
| 24 | Temporal separation "unambiguously" warrant | Approved | Warrant revised: Invariant #2 provides guarantee, not temporal separation alone |
| 25 | Invariant #1 orchestration/domain boundary | **Rejected** | persist() is protocol (unconditional, content-agnostic, delegated), not domain logic. Semantic specificity is the criterion. |
| 26 | Invariant #9 + parser position vs meaning | **Rejected** | Provenience ≠ meaning. Temporal scope of "meaning equals representation" clarified. |
| 27 | Missing canonical execution identity | Approved | Execution identity addendum: `run_id` declared as canonical execution identifier |

---

## Invariants — Delta from Session

### Invariant #1 — Clarified (Attack #25)

**Before:** `pipeline.py` only orchestrates — no business logic execution.

**After:** Orchestration includes: sequencing stage calls, enforcing the persist → reload transition protocol between stages, routing typed schemas as inputs, and propagating stage failures as typed exceptions. Business logic means: the domain operations performed by agents — generating plans, applying patches, scanning repositories, executing git commands. The pipeline sequences; it never implements agent-domain operations. The persist → reload transition sequence is orchestration by delegation: `pipeline.py` sequences the calls; `workspace.py` executes persistence; Pydantic schemas execute validation.

**Attack #25 — rejected (June 10):** The attack claimed persist() is intrinsically domain logic because it determines recoverable state. The criterion for "domain logic" is **semantic specificity to the problem domain** — not magnitude of effect on system properties. Persist() is unconditional (applies to every stage output regardless of content), content-agnostic (orchestrator does not inspect or filter stage outputs based on domain criteria), and delegated (workspace.py executes). An operation is not domain logic merely because it has strong effects on recoverability, replay, and distribution. It becomes domain logic when those effects are conditioned on domain-specific knowledge. The orchestrator's rule — "persist all stage outputs, always, before the next stage" — contains no domain knowledge. Semantic ownership (what the artifact contains, how it is produced) and protocol ownership (when it becomes canonical, under which rule) are orthogonal responsibilities assigned to separate components by design.

### Invariant #2 — Addendum added

**Addendum — Round-trip stability:** All schemas that cross stage boundaries must satisfy round-trip stability: for any validly-constructed instance `m`, `Model.model_validate_json(m.model_dump_json()) == m`. Validators in inter-stage schemas must be deterministic and must not depend on construction-time context, external state, environment variables, or any source not present in the serialized fields.

**Enforcement model (V1):** Development convention verified by CI tests. Upgrade path: `_assert_round_trip()` in `pipeline.py`.

### Invariant #3 — Fully restructured, then clarified (Attack #22)

- **Model A declared:** artifact consumed by N+1 = value deserialized from what N persisted. External mutations are corruption.
- **Conformance criterion:** Structural code property — no data-level test can distinguish loaded-from-disk from copied-in-memory under Model A.
- **Scope:** Within a single pipeline run. Cross-version loading requires ADR.
- **Contract vs Persistence boundary:** Enforced by temporal separation — `SchemaValidationError` at production time, `ValidationError` at reload time. Within reload-time failures, truncation/corruption/evolution are indistinguishable (known diagnostic gap).
- **Attack #22 — behavioral consequences (June 10):** The behavioral equivalence of the two implementations (load-from-disk vs in-memory copy) is acknowledged for valid single-machine runs without failures. The architectural status of this invariant derives from two concrete observable properties: (1) **pipeline resumability** — a run interrupted after Stage N's persist can be resumed from the persisted artifact; an in-memory copy implementation cannot support this; (2) **distributed execution correctness** — P3 workers operating on separate machines must read persisted artifacts; they have no access to another worker's in-memory state. Both properties are observable behavioral differences under their respective conditions and are required by the roadmap. Violations produce no immediate test failure but foreclose these properties without making that foreclosure observable.
- **Attack #23 — ADR-01 trigger promotion (June 10):** The composition between Invariant #2 (round-trip) and Invariant #3 (source-of-truth) reveals a load-bearing assumption: the single-run scope restriction holds as long as all artifacts are consumed within the pipeline run that produced them. P2 (dogfooding) is the architectural moment at which cross-version loading becomes a system requirement by construction — experiment artifacts will be compared, reviewed, and potentially reprocessed across software versions. ADR-01 preconditioner promoted from "empirical evidence" to "P2 entry condition."
- **Attack #24 — temporal separation warrant corrected (June 10):** The claim that a ValidationError at reload "unambiguously" excludes agent fault requires Invariant #2 as warrant, not "because production-time validation already passed" alone. Same-schema case: round-trip stability (Invariant #2) guarantees valid production → valid reload under schema V; reload failure → persistence corruption. Cross-schema case: agent correctly implemented schema V1; incompatibility under V2 is evolution, not agent contract violation. The diagnostic gap (truncation vs corruption vs evolution) is orthogonal — it is sub-classification, not agent guilt.
- **Attack #27 — execution identity addendum (June 10):** The architecture lacked an explicit canonical entity for execution identity. `run_id` is declared as the canonical execution identifier — assigned at pipeline initiation before any artifact is persisted. The `runs/<run_id>/` directory is the execution boundary. `workspace`, `commit_anchor`, and `software_version` are attributes of an execution, not identity candidates. Two executions with identical content but distinct `run_id`s are distinct executions by definition. Cross-version execution identity is deferred to ADR-01.

### Invariant #9 — New, then clarified (Attack #21)

**Inter-stage schemas are pure DTOs:** Their complete semantic content is defined by their serialized fields. No implicit semantic dependency on external state, execution context, filesystem, or in-memory references is permitted. Meaning equals representation, by architectural definition.

**Attack #21 — scope clarification (June 10):** "Meaning equals representation" refers to **representational completeness** — the DTO fully specifies the work to be performed, independently of the runtime state on which that work will be executed. Operational applicability (whether execution succeeds on a given repository, commit, or workspace) is **not** part of the DTO's semantic content. It is verified at execution time by the validation stage. Execution context (repository identity, commit SHA, workspace path) is a workflow-level concern managed at the experiment or orchestration level, not encoded in inter-stage schemas. This clarification confirms the existence of a **Model C**: the DTO is the unit of specification; the experiment is the unit of context. Mixing them would violate separation of concerns.

**Attack #26 — temporal scope of "meaning" (June 10, rejected):** The attack claimed that the parser's use of positional information to select the first JSON object creates an implicit semantic dependency violating Invariant #9. The dependency does not hold because: (1) **provenance ≠ meaning** — positional information is used during production but no downstream stage needs it; (2) the parser contract (AC2(h)) documents the behavior of multiple valid objects explicitly (first taken, subsequent ignored); (3) Invariant #9 governs the DTO **in transit between stages**, not the mechanism that produced it. Once produced, the DTO is semantically self-contained. The temporal scope of "meaning equals representation" is clarified: it applies after production is complete, not during production.

---

## Architectural Decisions (Explicit)

| Decision | Rationale |
|---|---|
| **Positional extractor** over document parser | LLM outputs are arbitrary text — cannot assume valid JSON at root |
| **Module-level canonicity** over function-level | `parse_llm_response()` handles dict-rooted case; module `orchestrator/llm/` is the canonical location |
| **Temporal separation** as contract/persistence boundary | `SchemaValidationError` (production) vs `ValidationError` (reload) — agent fault exclusion guaranteed by Invariant #2 (round-trip stability), not by temporal separation alone |
| **Model A** (disk as transactional boundary) | Recovery, auditability, and reproducibility require disk to be the source of truth, not memory |
| **Round-trip stability as V1 convention** | Runtime enforcement (`_assert_round_trip()`) deferred until team scale or empirical evidence justifies it |
| **DTO purity** (meaning = serialized fields) | Consequence of Model A — if semantics depended on external state, Invariant #3 would be incoherent |
| **Model C** (DTO = specification, experiment = context) | Execution context is workflow-level, not DTO-level. Invariant #9 governs representational completeness, not execution determinism |

---

## Deferred ADRs

| ADR | Precondition |
|---|---|---|
| Schema version identity in RunMetadata | **P2 entry condition** — dogfooding produces artifacts that cross software versions by construction (comparison, review, reprocessing). Resolution must precede P2, not follow empirical failure. Promoted from "empirical evidence" per Attack #23. |
| Runtime round-trip verification (`_assert_round_trip()`) | Team exceeds coordinated discipline, or dogfooding reveals violations |
| Reload-time diagnostic attribution (truncation/corruption/evolution) | Same as ADR-01 |

---

## Readiness for Implementation

The architecture is in condition to implement Issue A. The parser that implements it introduces the temporal separation that Invariant #2 (addendum) already names formally. Implementation will not discover new structural contradictions — only concrete modeling decisions, which is exactly where the cost should live from this point forward.

**Next step:** Implement `parse_llm_response()` per `issue-a-design.md` (11 ACs, all specified).
