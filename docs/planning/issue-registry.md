# Issue Registry — PatchForge Core

> **Date:** 2026-07-11
> **Source:** `docs/planning/roadmap.md` (Core P4–P5) + `docs/planning/adversarial-audit.md` (P0–P2 provenance)
> Live inventory of PatchForge Core issues with status, priority, and preconditions. P4/P5 entries are terse — full ACs are written when each becomes a GitHub issue.

> **Note on historical entries:** `Source:` lines on completed issues point at the doc that was live when the issue was created. Some of those docs have been retired (`roadmap-phase2.md`, `dogfooding-vision.md`, `issue-a-design.md`) — the references stay verbatim as historical record. Only new entries reference the current `roadmap.md`.

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

### ✅ Issue #145 — Hardening Sprint: Provider visibility, --force-provider, test collection fix
- **Priority:** P2 | **Status:** ✅ **Completed**
- **Goal:** Three post-dogfooding fixes: (1) `pytest.importorskip` in `test_github.py`, (2) `ProviderChainResult` dataclass for provider failure tracking + Rich error panel in preview, (3) `--force-provider` CLI flag orthogonal to `risk_level` (does not mutate risk_level or affect high-risk gating).
- **Source:** Exp 004 dogfooding
- **Precondition:** None
- **Files touched:** 5 code (`providers.py`, `applier.py`, `executor/__init__.py`, `main.py`, `preview.py`) + 3 test files (11 new tests)
- **Known gap:** `log_event` for `force_provider` not wired to `pipeline.jsonl` — executor lacks pipeline trace context. Documented in `docs/context/discoveries.md`.

### ✅ Issue #149 — Workspace Hash Inconsistency (Windows)
- **Priority:** P2 | **Status:** ✅ **Completed**
- **Goal:** Fix `_workspace_hash()` producing different hashes for the same CWD on Windows due to inconsistent path casing normalization. Normalize with `.as_posix()` + `.lower()` on Windows before hashing.
- **Root cause:** `Path.resolve()` on Windows with `strict=False` does not guarantee consistent casing normalization. `Path.cwd()` preserves shell-provided casing. Two invocations from the same repo could produce different workspace paths.
- **Files:** `src/orchestrator/schemas/config.py` (normalize in `_workspace_hash`), `tests/test_workspace_safety.py` (regression test)

### ✅ Issue #151 — Validator timeout produces no actionable feedback
- **Priority:** P2 | **Status:** ✅ **Completed**
- **Goal:** Make validator timeout configurable via CLI/env/config, show per-tool spinner progress, surface actionable timeout details in the final Panel, and short-circuit remaining tools on timeout.
- **Files:** `config.py`, `validator_output.py`, `runners.py`, `validator/__init__.py`, `validation_workspace.py`, `preview.py`, `main.py`, `tests/test_validator_timeout.py`

### ✅ Formalize Experiment Schema (debt P2→P3)
- **Priority:** P2 | **Status:** ✅ **Completed**
- **Goal:** Formalize "Experiment" as a schema concept carrying execution context (commit SHA, repository identity, workspace path, run ID).
- **Source:** `dogfooding-vision.md` (Deferred section)
- **Precondition:** Experiment Artifacts schema complete

---

## P3 — Async Workers & CI/CD Integration

### ✅ Issue #140: Storage, WAL & Core Persistence
- **Priority:** P3 | **Status:** ✅ **Completed**
- **Commit:** `f437f46`
- **Branch:** `fix/issue-140-core-persistence`
- **Goal:** Resolve critical findings from the 6th adversarial audit — ensure all `apply.json` writes use atomic `_wal_write()` with guaranteed fsync, and fix missing `encoding="utf-8"` in legacy pipeline persistence.
- **Scope on implementation:** 3 remaining `write_artifact("apply.json")` calls converted to `_wal_write()`; 2 `write_text()`/`read_text()` calls in `pipeline.py` given explicit UTF-8 encoding. All other originally scoped fixes (H-6, H-7a/b, M-7) were already present in the codebase.
- **Files:** `src/orchestrator/commands/apply.py`, `src/orchestrator/pipeline.py`

### ✅ Issue #142: Post-Audit Remaining Fixes
- **Priority:** P3 | **Status:** ✅ **Completed**
- **Branch:** `fix/issue-142-post-audit-fixes`
- **Goal:** Resolve remaining actionable findings (H-2, H-5, H-8, H-9) from the 6th adversarial audit — patchforge branch naming with issue_number support, repository locks with retry, stale TODO-B3 comments, and environment variable enforcement.
- **Scope on implementation:** 42 insertions across 3 files. `apply.py` — branch format `patchforge/{run_id}[/issue_{issue_number}]`, repo lock acquire/release with worker_id, PATCHFORGE_WORKSPACE env guard. `github.py` — token validation moved inside `__init__`. `lock.py` — reads REPO_LOCK_ENABLED and WORKER_ID from env.
- **Files:** `src/orchestrator/commands/apply.py`, `src/orchestrator/clients/github.py`, `src/orchestrator/storage/lock.py`

### ✅ Issue #181 — Docker Containerization
- **Priority:** P3 | **Status:** ✅ **Completed**
- **PR:** #182 | **Branch:** `feat/issue-181-docker-containerization` | **Merged:** 2026-06-29
- **Goal:** Package core (orchestration, git wrappers, schema validation) as standalone container for portable execution in GitHub Actions, ECS, or Kubernetes.
- **Source:** `roadmap-phase2.md`
- **Precondition:** All P0 + P1 + P2 complete
- **Scope on implementation:** 3 new files (`.dockerignore`, `Dockerfile`, `docker-entrypoint.sh`), README.md Docker section. Single-stage `python:3.12-slim` image; non-root `patchforge` user (UID 1000); entrypoint handles workspace init, git identity, credential helper, API key validation with scan/doctor skip, and HOME redirection for arbitrary UID remapping via `--user`.
- **Files:** `Dockerfile`, `docker-entrypoint.sh`, `.dockerignore`, `README.md`

### ✅ Issue #183 — CI/CD Reusable Workflow + Docker Execution
- **Priority:** P3 | **Status:** ✅ **Completed**
- **Branch:** `feat/issue-183-ci-cd-reusable-workflow`
- **Goal:** Reusable `workflow_call` workflow backed by Docker, with `patchforge ci` CLI command. Any repo can add PatchForge CI/CD with a 15-line caller workflow.
- **Source:** `roadmap-phase2.md`
- **Precondition:** Docker Containerization complete (Issue #181)
- **Architecture:** Container/runner boundary separation — pipeline runs in Docker (no `gh`, no `git push`), GitHub API calls on runner.
- **Files:** `src/orchestrator/commands/ci.py`, `src/orchestrator/schemas/ci_result.py`, `src/orchestrator/main.py`, `.github/workflows/patchforge-pipeline.yml`, `.github/workflows/patchforge-on-label.yml`, `docs/ci-cd-setup.md`, `docker-entrypoint.sh`, `tests/test_ci_command.py`

#### Technical debt logged in `docs/context/discoveries.md`
| # | Item | File | Impact |
|---|------|------|--------|
| 1 | `git add -A` stages untracked files with `--allow-dirty` | `ci.py:479` | Low — CI workflow doesn't pass the flag; matches existing pattern in `work_queue.py:405` |
| 2 | No `--force-provider` in `ci` command | `ci.py` | Low — debugging tool for interactive use, not needed for automated CI runs |
| 3 | `latest` Docker tag in workflow default | `patchforge-pipeline.yml:26` | Low — callers can pin via `patchforge-image` input; version-tagged publishing a separate issue |

### ✅ Issue #198 — Asymmetric Risk Gates (Light)
- **Priority:** P3 | **Status:** ✅ **Completed** | **PR:** #199
- **Goal:** Informational `auto_apply_eligible` field on `RunMetadata` computed via shared `compute_auto_apply_eligible()` in `artifacts.py`. Low-risk changes flagged as auto-PR eligible; high-risk require manual review. `DEFAULT_TIMEOUT` bumped 300→450s (D-004 remediation).
- **Source:** `roadmap-phase2.md`

### ✅ Issue #171 — GitHub Actions Pipeline Workflow
- **Priority:** P3 | **Status:** ✅ **Completed**
- **Goal:** `.github/workflows/patchforge-pipeline.yml` reusable `workflow_call` + `patchforge-on-label.yml` thin caller for PatchForge's own repo.

### ✅ Issue #212 — Tech Debt Closure + CI Apply Hardening
- **Priority:** P3 | **Status:** ✅ **Completed**
- **Goal:** Close 7 verified `discoveries.md` entries; replace `git add -A` in ci.py with targeted staging via `parse_diff_files()`; centralize `PROJECT_ROOT` in `orchestrator/paths.py`.

### ✅ Issue #219 — CB Thread-Safety Gaps Pre-P3
- **Priority:** P3 tech-debt closure | **Status:** ✅ **Completed** | **PR:** #220
- **Goal:** `_sqlite_connect()` opt-in `check_same_thread=False` + `SqliteCircuitBreakerStore._conn_lock`; `_registry_lock` in `circuit_breaker_for()`; `_init_lock` in `providers._init_circuit_breakers()`. Lock ordering documented. 2 regression tests.

---

## P4 — Trust & Configuration

> Order of implementation defined in `docs/planning/roadmap.md`. Each entry becomes a full-AC GitHub issue at pickup time.

### ✅ Issue #226: Qualitative Risk Gates (idea 2)
- **Priority:** P4 | **Status:** ✅ **Completed**
- **PR:** #227
- **Goal:** Extend `check_plan_gate()` with a file-semantic taxonomy (`schemas/*` = HIGH, `tests/*` = LOW, etc.) so risk classification uses richer criteria than `DANGEROUS_PATTERNS`.
- **Source:** `docs/planning/roadmap.md`
- **Precondition:** None (extends #198 asymmetric risk gates).
- **Non-goals:** No auto-merge/auto-apply real execution; no code semantics interpretation (Scout territory); no `pipeline.py` changes.

### ✅ Issue #228: IssueContract ADR (idea 6)
- **Priority:** P4 | **Status:** ✅ **Completed**
- **PR:** #229
- **Goal:** ADR-0005 + `IssueContract` schema in `schemas/issue.py` defining the canonical issue representation across all three sources (human markdown, GitHub API, future Scout). Round-trip stable, DTO pure. No pipeline consumer changes in this issue.
- **Source:** `docs/planning/roadmap.md`
- **Precondition:** None.
- **Non-goals:** No adapter implementation (GitHub Issue → IssueContract) yet; no Scout code; no pipeline consumption.

### ✅ Issue #230: Provider Registry (idea 9)
- **Priority:** P4 | **Status:** ✅ **Completed**
- **PR:** #231
- **Goal:** Make the models in `providers.py` configurable via a `providers` section in `orchestrator.json`, with current constants as defaults. Model appears in `run.json` for audit.
- **Source:** `docs/planning/roadmap.md`
- **Precondition:** None.
- **Non-goals:** No new providers or custom endpoints; no multi-model cost table; no changes to fallback chain or risk-level routing. Override of Claude records `cost_llm: null` + warning rather than a wrong number.

### Audit Bundle Export (idea 7)
- **Priority:** P4 | **Status:** 📐 Scoped
- **Goal:** `patchforge export-audit <run_id>` produces `audit-<run_id>.tar.gz` + `manifest.json` with SHA-256 of every artifact, PatchForge version, `schema_version`, providers used, `commit_anchor`, timestamp. Optional GPG signing via `--sign`. `--verify` recomputes hashes.
- **Source:** `docs/planning/roadmap.md`
- **Precondition:** Provider Registry complete (audit manifest must record the exact model used).
- **Non-goals:** No upload to external services (S3, artifact registries); no multi-run chain of custody; no RFC 3161 timestamping.

### Approval Provenance (idea 10)
- **Priority:** P4 | **Status:** 📐 Scoped
- **Goal:** Two additive `RunMetadata` fields — `triggered_by` and `approved_by` — captured from `github.actor` in CI and `git config user.*` locally. PR body includes the provenance line. Additive with default per ADR-0004 (no `schema_version` bump).
- **Source:** `docs/planning/roadmap.md`
- **Precondition:** None (independent; strong synergy with Audit Bundle).
- **Non-goals:** No authorization policy or role checks; no multi-person approval flow; no cryptographic identity verification (GPG on commits already covers that per Invariant #6).

---

## P5 — Learning Pipeline

### Experiment Ledger (idea 4)
- **Priority:** P5 | **Status:** 📐 Scoped
- **Goal:** Persist an `ExperimentRecord` per run in `experiments.db` (SQLite via `_sqlite_connect()`). `patchforge stats [--last N]` reports success rate, top failure types, avg cost, avg duration.
- **Source:** `docs/planning/roadmap.md`
- **Precondition:** None. Append-only; `failure_types` field stays empty until Feedback Loop lands.
- **Non-goals:** No dashboards or graphical visualization; no external metric services (Prometheus, Datadog); no trend analysis or prediction (deferred as analytics half of Experiment Framework).

### Impacted-Test Selection (idea 8)
- **Priority:** P5 | **Status:** 📐 Scoped
- **Goal:** Two-level validation. `preview --fast-validation` runs only tests importing the changed files (via deterministic reverse import graph). `apply` and `ci` always run the full suite. `validation.json` records `scope: "impacted"` and the selected subset.
- **Source:** `docs/planning/roadmap.md`
- **Precondition:** None.
- **Non-goals:** No replacement of the full suite on any path ending in apply/PR (thesis guarantee); no coverage history or ML-based selection — deterministic import graph only; no pytest parallelization (single-threaded validator invariant preserved).

### Executor Feedback Loop (idea 3)
- **Priority:** P5 | **Status:** 📐 Scoped
- **Goal:** `ExecutorDiagnosis` DTO per failed task, classifying via a typed enum. V1 emits deterministic types (`SYNTAX_INVALID`, `FILE_NOT_FOUND`, `PROVIDER_UNAVAILABLE`) + `UNCLASSIFIED` for the rest. Full 6-value enum defined for future LLM classifier.
- **Source:** `docs/planning/roadmap.md`
- **Precondition:** Ledger (feeds `failure_types`).
- **Non-goals:** No automatic retry logic — classifies only; no LLM classifier in V1; no `suggested_ac_refinement` field; no changes to the Architect contract.

### AC Compiler (idea 5)
- **Priority:** P5 | **Status:** 📐 Scoped
- **Goal:** Enrich `IssueInput` ACs with file/symbol anchors resolved deterministically via `ast.parse()` and symbol search, producing a `CompiledIssue` that *composes* `IssueContract`.
- **Source:** `docs/planning/roadmap.md`
- **Precondition:** IssueContract ADR complete (`CompiledIssue` composes it).
- **Non-goals:** Deterministic-only in V1 — only anchors ACs that literally name a symbol or path; no semantic prose→construct mapping (that's Scout territory, not a future extension); no auto-generated DO-NOT constraints; no linting or rejection of vague issues.

---

## Deferred (with explicit conditions)

### TypeScript Support Spike (idea 11)
- **Priority:** Deferred | **Status:** 📐 Spike-scoped
- **Goal:** 1–2 page inventory of every pipeline point that assumes Python + one manual E2E run against a small TS repo via `--issue-file`. Go/no-go decision based on the spike.
- **Precondition:** All of P4/P5 complete.
- **Non-goals:** No implementation commitment from the spike itself; no scanner TS support in any case (scan stays Python-only); no other languages until TS validates the generalization path.

### Defense in Depth (Auto-seeding characterization tests)
- **Priority:** Deferred | **Status:** 📐 Scoped
- **Goal:** Auto-seed characterization tests for uncovered code; shadow patching for untestable legacy functions.
- **Source:** `docs/planning/roadmap.md` (Deferred section; preserves the original deferral documented since P2)
- **Precondition:** Empirical evidence from dogfooding of a real test-zero legacy repo reveals concrete failure modes. Not driven by intuition.

### Experiment Framework & Metrics — analytics half
- **Priority:** Deferred | **Status:** 📐 Scoped
- **Goal:** Trend tracking, prediction, dashboarding over the `experiments.db` ledger. Half of the original P5 Experiment Framework whose persistence + CLI-reporting half ships as the Ledger (P5-1).
- **Source:** `docs/planning/roadmap.md` (Deferred section; supersedes the original P5 Experiment Framework entry)
- **Precondition:** Ledger complete + enough historical runs accumulated to justify the investment.
