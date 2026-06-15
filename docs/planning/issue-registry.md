# Issue Registry — PatchForge Phase 2 & Beyond

> **Date:** 2026-06-13
> **Source:** Roadmap decomposition (`roadmap-phase2.md`) + adversarial audit (`adversarial-audit.md`)
> **Total:** 20 issues (12 completed, 0 specified, 8 scoped but needing detailed ACs)

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

### ✅ T-02: Atomic Rollback Validation
- **Priority:** P0 | **Status:** ✅ **Completed**
- **PR:** #81
- **Goal:** Implement a reliable rollback primitive for the Executor to ensure the repository returns to a clean state upon failure.
- **Source:** `roadmap-phase2.md`
- **Precondition:** None

### ✅ T-01: Path Traversal Hardening
- **Priority:** P0 | **Status:** ✅ **Completed**
- **PR:** #86
- **Goal:** Enforce strict path construction contracts to prevent directory traversal attacks and workspace leakage.
- **Source:** `roadmap-phase2.md`
- **Precondition:** None

### ✅ T-07: Exception Hierarchy + Circuit Breaker
- **Priority:** P0 | **Status:** ✅ **Completed** | Part A ✅ (#71) | Part B ✅ (#87)
- **Goal:** Replace generic `RuntimeError` with typed exceptions (`PatchForgeError` base) and implement a circuit breaker for provider failures.
- **Source:** `roadmap-phase2.md`
- **Precondition:** None
- **Sub-issues:**
  - Part A ✅ — Exception hierarchy (structural) — completed #71
  - Part B ✅ — Circuit breaker implementation — completed #87
  - Part C ✅ — Tightening except clauses — completed #90

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

### ✅ Issue B: Issue Contracts (`--issue-file`)
- **Priority:** P1 | **Status:** ✅ **Completed**
- **Commit:** `5077252`
- **PR:** #93
- **Goal:** Enable the pipeline to consume human-written markdown issues as the primary source of truth.
- **Source:** `roadmap-phase2.md`
- **Precondition:** Issue A complete (parser must exist first)

#### Scope
- `parse_issue_markdown()` — parse frontmatter (title, severity, labels) + body from markdown
- `run_from_issue()` — bypass Scout, feed parsed issue directly to Claude
- `--issue-file` CLI flag on `plan` command
- `issue.md` copied to run directory as input artifact
- Escape braces in `str.format()` to prevent crash on `{`/`}` in body
- Reject whitespace-only files
- Line-aware `---` frontmatter delimiter parsing

#### Acceptance criteria
- [x] `parse_issue_markdown()` returns `IssueInput` with `title`, `severity`, `labels`, `body`, `raw`
- [x] Frontmatter-less input returns defaults (title="Untitled", severity="medium", labels=[])
- [x] Braces (`{}`) in body do not crash `run_from_issue()`
- [x] Whitespace-only content raises `ValueError("Issue file is empty")`
- [x] Mid-line `---` content inside frontmatter does not break delimiter detection
- [x] `run --issue-file <path>` plans from issue, not from scan findings
- [x] Missing `--issue-file` path exits with code 1
- [x] `ruff check .` — 0 errors
- [x] `pytest` — 288 passed, 2 skipped

#### Files changed
| File | Action |
|------|--------|
| `src/orchestrator/schemas/issue.py` | CREATE — IssueInput schema + parse_issue_markdown() |
| `src/orchestrator/agents/architect.py` | EDIT — add run_from_issue() + brace escaping |
| `src/orchestrator/commands/plan.py` | EDIT — add --issue-file path |
| `src/orchestrator/main.py` | EDIT — pass --issue-file from CLI to plan |
| `tests/test_issue_schema.py` | CREATE — 12 parser tests |
| `tests/test_architect.py` | EDIT — 4 run_from_issue tests (incl. braces) |
| `tests/test_v1_commands.py` | EDIT — 3 integration tests (flow, not-found, override) |

#### Non-goals
- Full end-to-end dogfooding (Experiment 001)
- Formal experiment schema
- Issue markdown validation beyond frontmatter structure

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

### ✅ ADR-01/3: Version Guard at Pipeline Load Point
- **Priority:** P2 entry | **Status:** ✅ **Completed**
- **Commit:** `1bae3dd`
- **Goal:** Implement the version guard that raises `SchemaVersionError` on mismatch per ADR-0004.
- **Precondition:** ADR-01/2 complete (`schema_version` field exists)

#### Scope
- `workspace.read_run_json()` deserializes and returns `RunMetadata` — no version logic in workspace
- `pipeline.py` compares `loaded.schema_version` against `CURRENT_SCHEMA_VERSION` after `workspace.read_run_json()`
- On mismatch: `raise SchemaVersionError(found=loaded.schema_version, expected=CURRENT_SCHEMA_VERSION)`
- `SchemaVersionError` already existed from T-07 Part A (#71), inherits from `PatchForgeError` with keyword-only `found`/`expected` args
- `CURRENT_SCHEMA_VERSION = 1` defined as constant in `src/orchestrator/schemas/artifacts.py`

#### Acceptance criteria
- [x] `SchemaVersionError` exists in `src/orchestrator/exceptions.py` with `found: int` and `expected: int`
- [x] `workspace.read_run_json()` contains no version comparison logic
- [x] `pipeline.py` raises `SchemaVersionError` when `loaded.schema_version != CURRENT_SCHEMA_VERSION`
- [x] `pipeline.py` proceeds normally when `loaded.schema_version == CURRENT_SCHEMA_VERSION`
- [x] Tests cover: valid load, future version load, past version load, no existing artifact
- [x] `ruff check .` — 0 errors
- [x] `ruff format --check .` — clean
- [x] `pytest` — 226 passed, 1 skipped

#### Files changed
| File | Change |
|------|--------|
| `src/orchestrator/schemas/artifacts.py` | Add `CURRENT_SCHEMA_VERSION = 1` |
| `src/orchestrator/exceptions.py` | Fix docstring reference: `run_metadata.py` → `artifacts.py` |
| `src/orchestrator/pipeline.py` | Add version guard in `execute()` with `try/except FileNotFoundError` |
| `tests/test_pipeline.py` | 4 guard tests (no-artifact, valid, future, past) |

#### Non-goals
- Comparison logic in `workspace.py`
- Automatic migration between versions
- Versioning schemas other than `RunMetadata`
- Deprecation policy

---

## P2 — Experimentation Infrastructure & Dogfooding

### ✅ Experiment Artifacts Schema
- **Priority:** P2 | **Status:** ✅ **Completed**
- **Commit:** `26b4155`
- **Goal:** Define `Verdict` schema and `write_verdict()` persistence utility; document canonical run directory layout.
- **Source:** `dogfooding-vision.md`
- **Precondition:** ADR-01/1/2/3 complete, Issue A complete

#### Scope
- `Verdict(BaseModel)` with `run_id`, `status`, `validation_passed`, `apply_succeeded`, `error_message`, `generated_at`
- `write_verdict(run_dir, verdict)` writes `verdict.json` and `verdict.md`
- No pipeline logic touched — `write_verdict()` is standalone
- Architectural debt: I/O co-located with schema (documented in `discoveries.md`)

#### Acceptance criteria
- [x] `Verdict` exists in `experiment.py` with all 6 required fields
- [x] Round-trip stable: `v.model_dump() == Verdict.model_validate_json(v.model_dump_json()).model_dump()`
- [x] `write_verdict()` writes `verdict.json` and `verdict.md` to `run_dir`
- [x] `write_verdict()` raises `FileNotFoundError` on missing directory
- [x] No `IssueMd` class exists — `issue.md` is a path convention only
- [x] `exp-artifact-layout.md` documents canonical layout with issue.md absence note and schema_version debt note
- [x] `Verdict` has no `schema_version` field
- [x] Debt entry added to `discoveries.md`
- [x] 5 tests cover: passed/failed construction, round-trip, both files written, FileNotFoundError
- [x] `ruff check .` — 0 errors; `ruff format --check .` — clean; `pytest` — 231 passed, 1 skipped
- [x] `git diff --stat` — exactly 4 files

#### Files changed
| File | Action |
|------|--------|
| `src/orchestrator/schemas/experiment.py` | CREATE — Verdict schema + write_verdict utility |
| `tests/test_experiment_schema.py` | CREATE — 5 tests |
| `docs/planning/exp-artifact-layout.md` | CREATE — canonical layout documentation |
| `docs/context/discoveries.md` | EDIT — add I/O debt entry |

#### Non-goals
- Wiring `write_verdict()` into `pipeline.py` (Experiment 001)
- Moving `write_verdict()` to `workspace.py` (documented debt)
- `IssueMd` Pydantic schema
- `schema_version` on `Verdict`

### ✅ Experiment 002 — Move write_verdict() to workspace.py
- **Priority:** P2 | **Status:** ✅ **Completed**
- **Branch:** `refactor/experiment-002-move-write-verdict`
- **Milestone:** Second successful dogfooding workflow. `write_verdict()` moved from `schemas/experiment.py` to `WorkspaceManager` in `workspace.py`, resolving debt entry #79.
- **Source:** `discoveries.md` (debt #79)
- **Precondition:** Experiment 001 complete, Issue B complete

#### Scope
- Add `WorkspaceManager.write_verdict(run_id, verdict)` method
- Remove standalone `write_verdict()` and `_write_verdict_markdown()` from `schemas/experiment.py`
- Update tests to use new method
- Mark debt #79 as resolved in `discoveries.md`

#### Acceptance criteria
- [x] `WorkspaceManager.write_verdict(run_id, verdict)` writes `verdict.json` and `verdict.md`
- [x] `schemas/experiment.py` contains only `Verdict(BaseModel)` — no I/O functions
- [x] `ruff check .` — 0 errors
- [x] `pytest` — 288 passed, 2 skipped
- [x] Debt entry #79 updated to resolved state

#### Bugs discovered
| # | Bug | Impact | Fix |
|---|-----|--------|-----|
| 1 | Executor skipped T2 when T1 was "already applied" | Incomplete patch, validation failed | Task dependency chain confused executor |
| 2 | Groq API 403 — key expired/rate-limited | Medium-risk tasks can't execute; pipeline stalls | ✅ Specified in #100 (provider fallback chain) |
| 3 | Risk budget defaults too restrictive (`max_files=2`, `risk_budget=low`) | Multi-file refactors blocked; manual `run.json` edit required | Add `--risk-budget` flag or auto-escalation for no-logic-change refactors |

#### Non-goals
- Wiring `write_verdict()` into `pipeline.py`
- `schema_version` on `Verdict`

### ✅ Experiment 001 — First successful self-modification workflow
- **Priority:** P2 | **Status:** ✅ **Completed**
- **Commit:** `887ad5a`
- **Branch:** `feat/experiment-001-dogfooding`
- **Milestone:** First successful self-modification workflow completed. PatchForge planned, validated, and applied a real code change to an isolated clone of itself — proving the dogfooding pipeline end-to-end.
- **Source:** `dogfooding-vision.md`
- **Precondition:** Experiment Artifacts schema complete, Issue B complete

#### Scope
- Write issue file with frontmatter (title, severity, labels, ACs)
- Clone target repo + configure .venv
- Execute full pipeline: scan → plan --issue-file → preview → apply
- Fix 3 bugs discovered during execution
- Document results as case study (CS-001)

#### Acceptance criteria
- [x] `patchforge scan` analyzes clone correctly
- [x] `patchforge plan --issue-file` generates a valid plan from human issue
- [x] `patchforge preview` generates patch.diff and validates (ruff + pytest)
- [x] Validation passed: 288 tests, 0 ruff errors
- [x] `patchforge apply` applies patch to clone successfully
- [x] Post-apply validation passes
- [x] Case study documented in docs/case-studies/001-...
- [x] 3 bugs found and fixed during the experiment

#### Bugs discovered

| # | Bug | Fix |
|---|-----|-----|
| 1 | `lineterm=""` corrupts diff headers | executor.py:414 |
| 2 | Trailing newline mismatch → no-op hunk | executor.py:354-356 |
| 3 | PATH resolution for target .venv | orchestrator.json |

#### Non-goals
- Formal experiment schema (deferred)
- Automated promotion to original repo
- CI/CD integration

### ✅ Experiment 003 — Add `--risk-budget` flag to `scan`
- **Priority:** P2 | **Status:** ✅ **Completed**
- **Commit:** *(pending push)*
- **Branch:** `feat/experiment-003-risk-budget-flag`
- **Goal:** Add `--risk-budget` CLI flag so users can specify risk budget
  at scan time instead of editing `run.json` manually.
- **Source:** Discovered during Experiment 002
- **Precondition:** Experiment 002 complete

#### Scope
- Add `--risk-budget` Typer option to `scan` CLI command
- Couple `max_files`/`max_diff_lines` to risk budget tiers
- Validate input; show valid options on invalid value
- 3 new tests: default, explicit medium, invalid value

#### Acceptance criteria
- [x] `patchforge scan . --risk-budget medium` writes `risk_budget: "medium"`,
  `max_files: 5`, `max_diff_lines: 250` in `run.json`
- [x] Without flag, behavior is identical to current (backward compatible)
- [x] Invalid `--risk-budget` value prints error with valid options and exits 1
- [x] `ruff check .` — 0 errors
- [x] `pytest` — 291 passed, 2 skipped
- [x] 3 new tests cover: default, explicit flag, invalid flag

#### Bugs discovered during experiment

| # | Bug | Impact | Fix |
|---|-----|--------|-----|
| 1 | LLM-generated code had E501 line length violations | ruff validation failed | Manually fixed line breaks in `main.py` and test docstrings |
| 2 | Test assertion error message didn't match actual output | pytest failed | Manually corrected assertion to match actual format |
| 3 | PowerShell `Set-Content -Encoding UTF8` adds BOM (U+FEFF) | Pydantic `model_validate_json` rejects with `json_invalid` | Used .NET `File.WriteAllText` with UTF8 no BOM |

#### Non-goals
- Changing `check_plan_gate()` or `check_patch_gate()` in `risk.py`
- Changes to scheduler or provider fallback (deferred to separate issues)
- No auto-escalation logic based on experiment history

### ✅ Issue #98: Executor DAG Scheduler — task dependency resolution
- **Priority:** P2 | **Status:** ✅ **Completed**
- **Branch:** `feat/issue-98-executor-dag-scheduler`
- **Goal:** Replace the flat sequential loop in `executor.run()` with a DAG-aware
  scheduler that resolves `Task.dependencies`, detects cycles, executes tasks in
  topological order (Kahn's algorithm, O(V²) deterministic), and propagates
  `SKIPPED` status when a dependency fails.
- **Source:** Bug #1 discovered in Experiment 002
- **Precondition:** Experiment 003 complete

#### Scope
- `TaskStatus(str, Enum)` with 5 members: `APPLIED`, `NOOP`, `SKIPPED`, `ERROR`, `PENDING_REVIEW`
- `_build_dag()` — validate all dependencies exist, build adjacency map
- `_topological_order()` — Kahn's algorithm, deterministic O(V²), raises `CycleDetectedError`
- DAG scheduler loop with per-file routing + worst-status aggregation for multi-file tasks
- `PENDING_REVIEW` blocks downstream (Option A — conservador, fail-safe)
- NOOP returns `diff=None` instead of placeholder string
- `CycleDetectedError` and `SchedulerInvariantError` in `exceptions.py`
- 11 scenario tests + 5 building-block tests (16 total)

#### Acceptance criteria
- [x] `ruff check .` — 0 errors
- [x] `ruff format --check .` — clean
- [x] `pytest` — 308 passed, 2 skipped (292 old + 16 new)
- [x] Linear DAG: all tasks APPLIED, order A→B→C
- [x] Diamond DAG: D executes after both B and C
- [x] Partial failure: B=ERROR → D=SKIPPED
- [x] Cycle: `CycleDetectedError` raised, no tasks execute
- [x] Missing dependency: `SchedulerInvariantError` in `_build_dag()`
- [x] NOOP: `status=NOOP`, `diff=None`, filtered by `preview.py` (`if change.diff`)
- [x] PENDING_REVIEW blocks downstream → downstream SKIPPED
- [x] Multi-file task partial error: aggregated ERROR → downstream SKIPPED
- [x] Long cascade (A→B→C→D): all downstream SKIPPED
- [x] `executor_output.applied` contains only APPLIED and NOOP
- [x] `executor_output.errors` contains ERROR and SKIPPED
- [x] `preview.py`, `plan.py`, `apply.py`, `pipeline.py` — zero changes
- [x] `discoveries.md` — Experiment 002 debt entry marked RESOLVED

#### Files changed
| File | Action |
|------|--------|
| `src/orchestrator/exceptions.py` | EDIT — add `CycleDetectedError`, `SchedulerInvariantError` |
| `src/orchestrator/schemas/executor_output.py` | EDIT — add `TaskStatus` enum, replace `FileChange.status` |
| `src/orchestrator/agents/executor.py` | EDIT — add DAG functions, scheduler loop, NOOP routing |
| `tests/test_executor_scheduler.py` | ADD — 16 tests |
| `docs/context/discoveries.md` | EDIT — mark debt resolved |

#### Non-goals
- Changes to `preview.py`, `plan.py`, `apply.py`, or `pipeline.py`
- Parallel execution (V1 invariant: single-threaded)
- Changes to `ArchitectOutput` or `Task` schema (dependencies field already existed)
- Provider fallback chain (Bug #2 from Experiment 002, deferred)

### ✅ Formalize Experiment Schema (debt P2→P3)
- **Priority:** P2 | **Status:** ✅ **Completed**
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
