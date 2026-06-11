# Issue Registry — PatchForge Phase 2 & Beyond

> **Date:** 2026-06-11
> **Source:** Roadmap decomposition (`roadmap-phase2.md`) + adversarial audit (`adversarial-audit.md`)
> **Total:** 18 issues (4 completed, 1 specified, 13 scoped but needing detailed ACs)

---

## Legend

| Status | Meaning |
|---|---|
| 🎯 **Specified** | Full ACs, scope, non-goals, and files defined |
| 📐 **Scoped** | Goal and priority known; ACs and files need decomposition |
| 🔒 **Blocked** | Waiting on precondition (e.g. ADR-01 must precede P2) |
| ✅ **Completed** | All ACs verified, merged |

---

## P0 — Core Stability (Phase 2 Blockers)

### T-02: Atomic Rollback Validation
- **Priority:** P0 | **Status:** 📐 Scoped
- **Goal:** Implement a reliable rollback primitive for the Executor to ensure the repository returns to a clean state upon failure.
- **Source:** `roadmap-phase2.md`
- **Precondition:** None

### T-01: Path Traversal Hardening
- **Priority:** P0 | **Status:** 📐 Scoped
- **Goal:** Enforce strict path construction contracts to prevent directory traversal attacks and workspace leakage.
- **Source:** `roadmap-phase2.md`
- **Precondition:** None

### T-07: Exception Hierarchy + Circuit Breaker
- **Priority:** P0 | **Status:** 📐 Scoped | Part A ✅ (#71)
- **Goal:** Replace generic `RuntimeError` with typed exceptions (`PatchForgeError` base) and implement a circuit breaker for provider failures.
- **Source:** `roadmap-phase2.md`
- **Precondition:** None
- **Sub-issues:**
  - Part A ✅ — Exception hierarchy (structural) — completed #71
  - Part B — Circuit breaker implementation (pending)
  - Part C — Tightening except clauses (pending)

### ✅ Issue A: Structured Contract Parsing
- **Priority:** P0 | **Status:** ✅ **Completed**
- **Goal:** Replace fragile `_extract_json()` with a robust, schema-aware parser that converts LLM text outputs directly into validated Pydantic models.
- **Source:** `issue-a-design.md` (11 ACs, complete)
- **Precondition:** None
- **Files:** `src/orchestrator/llm/parser.py`, `tests/`, `src/orchestrator/exceptions.py`

### ✅ DOC-01: Consolidate adversarial session documentation
- **Priority:** P0 | **Status:** ✅ **Completed**
- **Goal:** Finalize all documentation produced during the 27-attack adversarial session. Translate remaining Spanish content, bump metadata dates, and verify cross-references across all planning documents.
- **Precondition:** None (independent, can run in parallel with Issue A)

#### Scope
Six files need final verification or touch-up. No structural changes — only consistency, language, and metadata.

| File | What to do | Status |
|---|---|---|
| `docs/context/CONTEXT.md` | Bump `Last updated` to `2026-06-10`. Verify all 9 invariants match the delta sections in `adversarial-audit.md`. | Done |
| `docs/planning/adversarial-audit.md` | Translate line 69: "Ausencia de identidad canónica de ejecución" → "Missing canonical execution identity" | Done |
| `docs/planning/issue-registry.md` | Add this entry (DOC-01) to the registry | Done |
| `docs/product-thesis-v2.md` | No changes needed — verify it is referenced in `docs/index.md` | Done |
| `docs/index.md` | Add links to `docs/planning/issue-registry.md` and `docs/product-thesis-v2.md` in Quick Links | Done |
| `docs/planning/roadmap-phase2.md` | No changes needed — verify Critical path update references ADR-01 correctly | Done |

#### Acceptance criteria
- [x] `docs/context/CONTEXT.md` — Last updated date is `2026-06-10`. All 9 invariants match the audit delta sections.
- [x] `docs/planning/adversarial-audit.md` — Line 69 attack title is in English. No Spanish text remains in any planning document.
- [x] `docs/index.md` — Quick Links includes entries for `issue-registry.md` and `product-thesis-v2.md`.
- [x] `docs/planning/issue-registry.md` — This issue (DOC-01) is listed in the registry.
- [x] `ruff check .` — 0 errors (linting markdown-referenced code is unaffected).
- [x] No code files are modified. This issue is documentation only.

#### Files to change
| File | Change |
|---|---|
| `docs/context/CONTEXT.md` | Bump date, verify invariant text |
| `docs/planning/adversarial-audit.md` | Translate one line (line 69) |
| `docs/index.md` | Add links to `issue-registry.md` and `product-thesis-v2.md` |
| `docs/planning/issue-registry.md` | Add this entry as DOC-01 |

#### Non-goals
- Creating ADR-0004 (deferred to ADR-01/1)
- Rewriting or restructuring any document
- Adding new content beyond cross-reference links
- Modifying any source code, tests, or configuration

---

## P1 — Input Contracts

### Issue B: Issue Contracts (`--issue-file`)
- **Priority:** P1 | **Status:** 📐 Scoped
- **Goal:** Enable the pipeline to consume human-written markdown issues as the primary source of truth.
- **Source:** `roadmap-phase2.md`
- **Precondition:** Issue A complete (parser must exist first)

---

## P2 Entry Condition — ADR-01 (Schema Versioning)

> **Context:** ADR-01 was promoted from "empirical evidence precondition" to "P2 entry condition" per Attack #23. Dogfooding produces artifacts that cross software versions by construction. These three issues must be resolved before P2 begins.

### ✅ ADR-01/1: Write ADR-0004 — Schema Versioning Policy
- **Priority:** P2 entry | **Status:** ✅ **Completed**
- **Goal:** Produce the decision document that defines schema versioning format, scope, breaking change semantics, increment trigger, and mismatch behavior.
- **Source:** Adversarial audit Attack #23

#### Scope
ADR-0004 must answer exactly five questions:

1. **Format:** integer monotonic (`schema_version: int`). Not semver, not date-based.
2. **Which schemas carry it:** `RunMetadata` only. Stage-intermediate schemas (`ArchitectOutput`, `Plan`, etc.) are out of scope under Invariant #3's single-run restriction. This restriction is valid for V2; documented as known debt for P3.
3. **Breaking change definition:** field removal or rename = breaking (increments version). Field addition with default = additive (no increment).
4. **Increment trigger:** the `CURRENT_SCHEMA_VERSION` bump is part of the same commit that introduces the breaking change. Not a separate operation. Enforced by code review, not CI.
5. **Mismatch behavior:** `SchemaVersionError` (typed). No warning. No migration. No silent load.

#### Acceptance criteria
- [x] `docs/adr/ADR-0004-schema-versioning.md` exists with sections: Context, Decision, Consequences, Rejected alternatives, Known debt
- [x] Document specifies `schema_version: int` with initial value `1` on `RunMetadata`
- [x] Document defines "breaking change" with one breaking and one additive concrete example
- [x] Document names the increment trigger explicitly (same commit, code review enforcement)
- [x] Document names `SchemaVersionError` as the mismatch exception
- [x] Document has a "Known debt" section documenting the expiration of "RunMetadata only" at P3
- [x] No code modified in this issue

#### Files to change
| File | Change |
|------|--------|
| `docs/adr/ADR-0004-schema-versioning.md` | Create |
| `docs/index.md` | Add ADR-0004 entry in ADR table |

#### Non-goals
- Implement the field
- Implement the guard
- Define migration policy
- Version intermediate stage schemas

---

### ✅ ADR-01/2: Add `schema_version` to RunMetadata
- **Priority:** P2 entry | **Status:** ✅ **Completed**
- **Commit:** `2546e3b`
- **Goal:** Implement the `schema_version: int = 1` field on `RunMetadata` per ADR-0004.
- **Precondition:** ADR-01/1 complete (ADR-0004 must exist)

#### Scope
- Add `schema_version: int = 1` to `RunMetadata`
- Field has default `1` — existing artifacts without the field load correctly (Pydantic infers as `1`)
- No comparison logic in this issue

#### Acceptance criteria
- [x] `RunMetadata.schema_version` exists as `int` field with default `1`
- [x] Serialized `run.json` includes `"schema_version": 1`
- [x] An existing `run.json` without the field loads correctly with `schema_version=1`
- [x] Round-trip stability: `RunMetadata.model_validate_json(m.model_dump_json()) == m` for any valid instance
- [x] `ruff check` — 0 new findings
- [x] `pytest` — 222 passed / 1 skipped

#### Files changed
| File | Change |
|------|--------|
| `src/orchestrator/schemas/artifacts.py` | Add `schema_version: int = 1` |
| `tests/test_run_metadata.py` | Add 4 tests (default, serialization, backward compat, round-trip) |

#### Non-goals
- Version validation at load time (next issue)
- Incrementing the version (only applies at first breaking change)
- Modifying intermediate stage schemas

---

### 🎯 ADR-01/3: Version Guard at Pipeline Load Point
- **Priority:** P2 entry | **Status:** 🎯 **Specified**
- **Goal:** Implement the version guard that raises `SchemaVersionError` on mismatch per ADR-0004.
- **Precondition:** ADR-01/2 complete (`schema_version` field exists)

#### Scope
- `workspace.load_run()` deserializes and returns `RunMetadata` — no version logic in workspace
- `pipeline.py` compares `loaded.schema_version` against `CURRENT_SCHEMA_VERSION` after `workspace.load_run()`
- On mismatch: `raise SchemaVersionError(found=loaded.schema_version, expected=CURRENT_SCHEMA_VERSION)`
- `SchemaVersionError` inherits from `Exception` with attributes `found: int` and `expected: int`, with `# TODO: migrate to PatchForgeError in T-07`
- `CURRENT_SCHEMA_VERSION = 1` defined as constant in `src/orchestrator/schemas/run_metadata.py`

#### Acceptance criteria
- [ ] `SchemaVersionError` exists in `src/orchestrator/exceptions.py` with `found: int` and `expected: int`
- [ ] `workspace.load_run()` contains no version comparison logic
- [ ] `pipeline.py` raises `SchemaVersionError` when `loaded.schema_version != CURRENT_SCHEMA_VERSION`
- [ ] `pipeline.py` proceeds normally when `loaded.schema_version == CURRENT_SCHEMA_VERSION`
- [ ] Tests cover: valid load, future version load, past version load
- [ ] `ruff check` — 0 new findings
- [ ] `pytest` — 207+N passed / 1 skipped

#### Files to change
| File | Change |
|------|--------|
| `src/orchestrator/exceptions.py` | Add `SchemaVersionError` |
| `src/orchestrator/pipeline.py` | Add version guard after `workspace.load_run()` |
| `src/orchestrator/schemas/run_metadata.py` | Add `CURRENT_SCHEMA_VERSION = 1` |
| `tests/test_pipeline.py` | Version guard tests |

#### Non-goals
- Comparison logic in `workspace.py`
- Automatic migration between versions
- Versioning schemas other than `RunMetadata`
- Deprecation policy

---

## P2 — Experimentation Infrastructure & Dogfooding

### Experiment Artifacts Schema
- **Priority:** P2 | **Status:** 📐 Scoped
- **Goal:** Implement structured record for every run (`issue.md`, `plan.json`, `patch.diff`, `qa_logs`, `verdict.md`).
- **Source:** `dogfooding-vision.md`
- **Precondition:** ADR-01/1/2/3 complete, Issue A complete

### Experiment 001 (POC) — Clone Workflow
- **Priority:** P2 | **Status:** 📐 Scoped
- **Goal:** First controlled run: rename `_extract_json` to `_parse_llm_json` using the clone method.
- **Source:** `dogfooding-vision.md`
- **Precondition:** Experiment Artifacts schema complete

### Formalize Experiment Schema (debt P2→P3)
- **Priority:** P2 | **Status:** 📐 Scoped
- **Goal:** Formalize "Experiment" as a schema concept carrying execution context (commit SHA, repository identity, workspace path, run ID).
- **Source:** `dogfooding-vision.md` (Deferred section)
- **Precondition:** Experiment Artifacts schema complete

---

## P3 — Async Workers & CI/CD Integration

### Docker Containerization
- **Priority:** P3 | **Status:** 📐 Scoped
- **Goal:** Package core (orchestration, git wrappers, schema validation) as standalone container.
- **Source:** `roadmap-phase2.md`
- **Precondition:** All P0 + P1 + P2 complete

### CI/CD Integration
- **Priority:** P3 | **Status:** 📐 Scoped
- **Goal:** GitHub Actions / GitLab CI worker that listens for Issues, clones repo, executes plan → preview → validate, opens PR.
- **Source:** `roadmap-phase2.md`
- **Precondition:** Docker Containerization complete

### Asymmetric Risk Gates (Light)
- **Priority:** P3 | **Status:** 📐 Scoped
- **Goal:** Low-risk changes (`.md`, templates) → auto-PR; high-risk changes (schemas, core logic) → manual approval.
- **Source:** `roadmap-phase2.md`
- **Precondition:** CI/CD Integration complete

---

## P4 — Advanced Guardrails

### Qualitative Risk Gates
- **Priority:** P4 | **Status:** 📐 Scoped
- **Goal:** Classify risks by file type (e.g., `schemas/` = HIGH, `tests/` = LOW). Connect to async worker flow.
- **Source:** `roadmap-phase2.md`
- **Precondition:** Asymmetric Risk Gates complete

---

## P5 — Formalization

### Experiment Framework & Metrics
- **Priority:** P5 | **Status:** 📐 Scoped
- **Goal:** Track success rates, diff accuracy, and failure modes over multiple experiments.
- **Source:** `roadmap-phase2.md`
- **Precondition:** Experiment 001 complete

### Defense in Depth (Auto-seeding)
- **Priority:** P5 | **Status:** 📐 Scoped
- **Goal:** Auto-seed characterization tests for uncovered code; shadow patching for untestable legacy functions.
- **Source:** `roadmap-phase2.md`
- **Precondition:** Empirical evidence from dogfooding reveals real failure modes

---

## Appendix: Dependency Chain

```
P0                     P1           P2 entry              P2              P3            P4       P5
───                    ──           ────────              ──              ──            ──       ──
T-02 ──────────────────────────────────────────────────────────────────────────────────────────
T-01 ──────────────────────────────────────────────────────────────────────────────────────────
T-07 ──────────────────────────────────────────────────────────────────────────────────────────
Issue A ─────────────> Issue B ──> ADR-01/1 ─> ADR-01/2 ─> ADR-01/3 ─>
                                    │                       │
                                    │                       └──> Exp Schema ──> Exp 001 ──>
                                    │                                       │
                                    │                                       └──> Formalize Experiment ──>
                                    │
                                    └──> (Known debt: "RunMetadata only" expires at P3)
                                                                             │
                                                                             └──> Docker ──> CI/CD ──> Risk Gates ──>
                                                                                                                      │
                                                                                                                      └──> Qual Risk Gates ──>
                                                                                                                                           │
                                                                                                                                           └──> Exp Framework ──> Defense in Depth
```

**Sequential constraint:** A → B means B requires A as input, precondition, or dependency.
**Unconstrained items** (T-02, T-01, T-07) are independent and may be implemented in any order.
