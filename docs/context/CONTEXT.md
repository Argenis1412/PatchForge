# PatchForge — Project Context

> Last updated: 2026-07-08 | Session: D-006-executor-syntax-validation
> This document is the single source of truth for AI sessions. Read before any implementation work.

---

## Project at a Glance

**What:** PatchForge — AI-powered, safety-first code modification tool. Generates, validates, and applies patches through a deterministic Plan → Preview → Validate → Apply pipeline.

**Phase:** P3 — Async Workers & CI/CD Integration (P0/P1/P2 complete, all P3 blockers done)

**Stack:** Python 3.12+ | Pydantic schemas | Typer CLI | ruff + pytest QA

**CLI:** `patchforge` (primary), `orchestrator` (legacy alias)

**QA:** `pytest` → 665 passed, 2 skipped | `ruff check .` → 0 errors | `ruff format --check` → clean

**Key constraint:** Single-threaded, synchronous pipeline (invariant; Docker containerization complete in P3).

---

## Working Style

**Core rule:** The refactor is never the unit of work. The unit is a self-contained issue with limited scope and verifiable criteria.

**Risk distribution:** Each AI role (Clarifier, AC Challenger, Adversarial Reviewer, Diff Reviewer) has a distinct responsibility. No single AI has authority to design the whole solution.

**Rules:**
- Implement only what the issue requires. No unrelated refactors, no speculative improvements.
- Keep diffs minimal. Code, comments, commits, and PRs in English only.
- Conventional commits only (`feat`, `fix`, `docs`, `refactor`, `chore`).
- Behavior changes require tests. GPG-verified commits.
- **Golden Rule:** Implement the smallest correct change that satisfies all acceptance criteria.

> For the full 10-step workflow, QA gates, branch naming, and commit format, see `docs/context/Workflow.md`.

---

## Repository Structure

```
src/orchestrator/
├── agents/
│   ├── architect/         # Claude Sonnet 4.6 — generates task plan
│   ├── executor/          # Multi-LLM routing — applies changes
│   │   ├── __init__.py    # run() entrypoint + DAG scheduler
│   │   ├── applier.py     # Task prompt builder + file staging
│   │   ├── providers.py   # LLM provider chain (Gemini, OpenRouter, Claude)
│   │   ├── diffing.py     # Unified diff generation
│   │   └── validation.py  # Pre-diff ast.parse() syntax validation (D-006)
│   ├── scout/             # Gemini — repository analysis (legacy)
│   └── validator/         # ruff + pytest subprocess runners
│       ├── __init__.py    # run() entrypoint
│       ├── runners.py     # subprocess wrappers
│       └── summarizer.py  # LLM error summarization
├── circuit_breaker.py       # Per-provider circuit breaker (T-07B)
├── clients/
│   ├── anthropic_client.py
│   ├── gemini_client.py
│   ├── openrouter_client.py
│   └── bootstrap.py
├── commands/
│   ├── scan.py            # V1 deterministic scan (non-AI)
│   ├── plan.py            # V1 AI-assisted planning (+ --issue-file)
│   ├── preview.py         # Patch preview + validation (staging cleanup)
│   ├── ci.py              # Full CI pipeline: scan → plan → preview → apply (no push)
│   └── apply.py           # Patch application to target
├── observability/
│   ├── events.py
│   └── logging.py
├── scanners/
│   └── python.py          # V1 deterministic Python scanner
├── schemas/
│   ├── architect_output.py
│   ├── artifacts.py       # RunMetadata — run state source of truth
│   ├── config.py          # TargetConfig + TargetCapabilities
│   ├── executor_output.py
│   ├── findings.py        # ScanFindings — V1 deterministic scan schema
│   ├── issue.py           # IssueInput — human issue frontmatter parser
│   ├── git.py             # GitCommandResult, ValidationWorkspace, etc.
│   ├── pipeline_run.py
│   ├── ci_result.py       # CiResult — thin projection of RunMetadata for CI output
│   ├── risk.py
│   ├── scout_output.py
│   └── validator_output.py
├── doctor.py              # V1 readiness check
├── git.py                 # Pure git wrappers (no domain logic)
├── lifecycle.py           # Patch lifecycle state machine
├── main.py                # CLI surface
├── pipeline.py            # Central orchestrator (Pipeline class)
├── plan_validation.py     # Filesystem path validation for plans (D-001)
├── risk.py                # Plan gate + patch gate logic
├── safety.py              # Path-safety validation utilities
├── validation_workspace.py
└── workspace.py           # WorkspaceManager — disk layout

tests/                     (27 test files, 665+ tests)
```

---

## Current State

### V1 — Complete (May 27 – Jun 8)

22 issues implementing the deterministic CLI pipeline: doctor, scan, plan, preview, apply. No AI dependency in scan and doctor.

| # | Title | Date |
|---|-------|------|
| 18 | PatchForge thesis (initial scaffold) | May 27 |
| 20 | Enforce external workspace safety | Jun 2 |
| 21 | Deterministic Git safety primitives + V1 isolated validation | Jun 3 |
| 24 | Translate all Spanish to English | Jun 4 |
| 25 | V1 run-centric artifact persistence and commands | Jun 4 |
| 26 | Deterministic doctor command + V1 support gate | Jun 5 |
| 28 | API key warnings | Jun 5 |
| 30 | Doctor: TypeScript out-of-scope warning | Jun 5 |
| 31 | Doctor docstrings: edge cases + return value docs | Jun 5 |
| 33 | Doctor docstrings: all functions | Jun 5 |
| 37 | Fix architect: deprecated model + harden JSON parsing | Jun 6 |
| 39 | Failure-state handling and apply rollback | Jun 6 |
| 41 | V1 risk gates and patch size limits | Jun 6 |
| 9b | Patch lifecycle states for V1 | Jun 6 |
| 9c | Fix: block REBASEABLE patches from being applied in V1 | Jun 7 |
| 45 | Replace AI Scout behavior with deterministic V1 scan | Jun 7 |
| 49 | Implement `plan run_id` with bounded AI-assisted planning | Jun 7 |
| 51 | Fix: conditional preview status and pre-apply validation gate | Jun 7 |
| 53 | Deprecate and hide legacy `run` command | Jun 8 |
| 55 | Rename CLI from `orchestrator` to `patchforge` | Jun 8 |
| 57 | Update documentation to use `patchforge` CLI | Jun 8 |
| 59 | Fix remaining ruff violations (N818, E501, E402, I001) | Jun 8 |

### P0 — Core Stability (Jun 12–13)

| # | Title | PR |
|---|-------|----|
| 81 | Atomic Rollback Validation (T-02) | #81 |
| 85 | Path Traversal Hardening (T-01) | #86 |
| 87 | Circuit Breaker — LLM Provider Failure Isolation (T-07B) | #87 |
| — | Issue A: Structured Contract Parsing (parse_llm_response) | — |
| — | DOC-01: Consolidate adversarial documentation | — |

### P1 / P2 Entry — Contracts & Schema Versioning

| # | Title | PR |
|---|-------|----|
| 92 | Issue Contracts — `--issue-file` (Issue B) | #93 |
| 73 | ADR-01/1: Write ADR-0004 (Schema Versioning Policy) | — |
| 75 | ADR-01/2: Add `schema_version` to `RunMetadata` | — |
| 77 | ADR-01/3: Version Guard at Pipeline Load Point | — |

### P2 — Dogfooding & Hardening (Jun 13–25)

| # | Title | Details |
|---|-------|---------|
| — | Experiment Artifacts Schema | Verdict schema + write_verdict utility |
| — | Experiment 001 | First successful self-modification workflow |
| — | Experiment 002 | Move write_verdict() to workspace.py |
| — | Experiment 003 | Add `--risk-budget` flag to scan |
| 98 | Executor DAG Scheduler | Task dependency resolution (Kahn's algorithm) |
| 140 | Core Persistence (WAL atomic writes) | All apply.json uses `_wal_write()` |
| 142 | Post-Audit Remaining Fixes | Branch naming, repo locks, env guards |
| 145 | Hardening Sprint | Provider visibility, `--force-provider`, test fix |
| 151 | Validator timeout config + feedback | CLI `--validator-timeout`, env var, per-tool spinner, `timed_out` field |
| 159 | Fix empty `patch.diff` on re-execution of `preview` | Staging cleanup + empty-patch guard (#160) |
| 006 | `safety.py` docstrings | Module + private helper docstrings (#161) |

---

## Architecture Invariants

These must not change without a new ADR in `docs/adr/`:

1. **`pipeline.py` only orchestrates** — no business logic execution. Orchestration includes: sequencing stage calls, enforcing the persist → reload transition protocol between stages, routing typed schemas as inputs, and propagating stage failures as typed exceptions. Business logic means: the domain operations performed by agents — generating plans, applying patches, scanning repositories, executing git commands. The pipeline sequences; it never implements agent-domain operations. The persist → reload transition sequence is orchestration by delegation: `pipeline.py` sequences the calls; `workspace.py` executes persistence; Pydantic schemas execute validation.
   - **Why persist() is orchestration, not domain logic (clarified 2026-06-10):** The criterion for "domain logic" is **semantic specificity to the problem domain**, not magnitude of effect on system properties (recoverability, replay, distribution). Persist() is unconditional (applies to every stage output regardless of content), content-agnostic (the orchestrator does not inspect, evaluate, or filter stage outputs based on domain criteria), and delegated (workspace.py executes the operation; pipeline.py sequences the call). An operation becomes domain logic when its effects are conditioned on domain-specific knowledge. The orchestrator's persist decision rule — "persist all stage outputs, always, before the next stage" — contains no domain knowledge. **Semantic ownership** (what the artifact contains, how it is produced) belongs to the stage. **Protocol ownership** (when the artifact becomes canonical, under which rule) belongs to the orchestrator. These are orthogonal responsibilities. Assigning both to a single component would violate separation of concerns, not preserve it.
2. **Agents receive and produce typed Pydantic schemas** — no raw dicts between stages
   - **Round-trip stability (addendum):** All schemas that cross stage boundaries must satisfy round-trip stability: for any validly-constructed instance `m`, `Model.model_validate_json(m.model_dump_json()) == m`. Validators in inter-stage schemas must be deterministic and must not depend on construction-time context, external state, environment variables, or any source not present in the serialized fields. Conformance is verified by a round-trip stability test for each inter-stage schema in the test suite.
   - **Enforcement model (V1):** This is a development convention verified by tests in CI, not a runtime guarantee enforced by the pipeline. `default_factory` and `PrivateAttr` do not violate round-trip stability — the generated value is serialized and preserved across reloads. The upgrade path to runtime enforcement (an `_assert_round_trip()` call in `pipeline.py` after each persist) is available and should be adopted when the team size exceeds coordinated discipline, or when dogfooding reveals schemas that violate round-trip stability in practice.
3. **Every stage output is persisted to disk before the next stage runs. The persisted artifact is the source of truth for stage transitions: the next stage must load its input from the persisted artifact. Passing an in-memory object directly between stages is prohibited, even when the artifact has already been written. Persistence is a transactional boundary, not an audit side-effect.**
   - **Model A** is the intended semantic: the artifact consumed by stage N+1 must be the value produced by deserializing exactly what stage N persisted. External mutations to the persisted artifact between stages are **corruption**, not valid transitions.
   - **Conformance criterion:** Conformance is a structural code property, not a runtime data property. Under Model A, disk and memory contain identical content by construction; no data-level test can distinguish "loaded from disk" from "copied in memory." Conformance is verified by two mechanisms:
       1. **Code inspection** — the pipeline must contain no execution path from stage N's persist call to stage N+1's invocation that does not pass through `workspace.load()`.
       2. **Call-sequence integration test** — asserts that `workspace.load()` is called during the transition and that the value passed to stage N+1 is equal (by Pydantic model equality) to the value returned by `workspace.load()`.
   - The mutation test previously specified is **withdrawn**: it verifies disk-reading behavior under Model B (disk as live source, where external mutations are valid) and must not be used as a conformance criterion for this invariant.
   - **Scope:** the source-of-truth guarantee applies to stage transitions within a single pipeline run. If an artifact persisted under an incompatible schema version is loaded, Pydantic raises a `ValidationError` at load time before any stage transition proceeds. This is the enforcement mechanism for the schema boundary — a hard failure, not silent corruption. The system does not verify schema version proactively; it enforces it structurally. Cross-version artifact loading — the ability to reload artifacts produced by older schema versions using current code — requires an explicit policy (schema versioning, migration, or formal expiration) introduced via ADR when it becomes a system requirement.
   - **Contract vs Persistence boundary:** The architectural boundary between contract validation (agent ↔ agent) and persistence decoding (disk → runtime) is enforced by **temporal separation**, not exception-type differentiation. `SchemaValidationError` (Issue A) captures contract violations at production time — before an artifact is written. A `ValidationError` at reload time cannot be an agent contract violation. This guarantee rests on two foundations:
       1. **Same-schema case** — Invariant #2 (round-trip stability, deterministic validators, no external state) guarantees that a validly-produced artifact under schema V always survives reload under schema V. A reload failure under same-version conditions therefore indicates persistence corruption, not agent error.
       2. **Cross-schema case** — if the schema version changed between production and reload, the agent correctly implemented the schema it was given; the incompatibility is a deployment or evolution issue, not an agent contract violation.
   In neither case is the agent at fault. The word "unambiguously" refers exclusively to this exclusion of agent fault. It does not claim certainty over the sub-classification between corruption, truncation, and evolution — this remains a **known diagnostic gap**: the pipeline terminates on any `ValidationError` before an invalid stage transition proceeds, regardless of the root cause within persistence/evolution failures.
   - **Behavioral consequences (clarified 2026-06-10):** The behavioral equivalence of load-from-disk vs in-memory copy is acknowledged for valid single-machine runs without failures. The architectural status of this invariant derives from two concrete observable properties:
       1. **Pipeline resumability** — a run interrupted after Stage N's persist can be resumed from the persisted artifact; an in-memory copy implementation cannot support this.
       2. **Distributed execution correctness** — P3 workers operating on separate machines must read persisted artifacts; they have no access to another worker's in-memory state.
   Both properties are observable behavioral differences under their respective conditions and are required by the roadmap. Violations produce no immediate test failure but foreclose these properties without making that foreclosure observable. Happy-path behavioral equivalence is not a criterion for invariant status: architectural invariants may protect properties observable only under specific conditions (crash, distribution, authorization).
   - **Execution identity addendum (clarified 2026-06-10):** `run_id` is the canonical execution identifier. Assigned at pipeline initiation, before any artifact is persisted. The `runs/<run_id>/` directory is the execution boundary. `workspace`, `commit_anchor`, and `software_version` are attributes of an execution, not identity candidates. Two executions with identical content but distinct `run_id`s are distinct executions by definition. Cross-version execution identity — determining whether two artifacts produced by different software versions represent "the same execution" — is deferred to ADR-01.
4. **`main.py` is CLI surface only** — no business logic
5. **`git.py` is a pure command wrapper** — no domain logic, no `run.json` access
6. **All commits are GPG-verified**
7. **Conventional commits only** (`feat`, `fix`, `docs`, `refactor`, `chore`)
8. **English only** — code, comments, commits, PRs
9. **Inter-stage schemas are pure DTOs** — All schemas that cross stage boundaries are pure Data Transfer Objects. Their complete semantic content is defined by their serialized fields. No implicit semantic dependency on external state, execution context, filesystem, or in-memory references is permitted in inter-stage schemas. Any information a stage needs must be explicitly present in its input schema. Meaning equals representation, by architectural definition, for all inter-stage schemas.
   - **Scope of "meaning" (clarified 2026-06-10):** "Meaning equals representation" refers to **representational completeness** — the DTO fully specifies the work to be performed, independently of the runtime state on which that work will be executed. Operational applicability (whether execution succeeds on a given repository, commit, or workspace) is not part of the DTO's semantic content. It is verified at execution time by the validation stage. Execution context (repository identity, commit SHA, workspace path) is a workflow-level concern managed at the experiment or orchestration level, not encoded in inter-stage schemas. This distinction is known as **Model C**: DTO = specification unit, experiment = context unit.
   - **Temporal scope (clarified 2026-06-10):** "Meaning equals representation" governs inter-stage schemas **in transit between stages — after production is complete**. The production mechanism may use information not present in the resulting DTO (position within LLM text, cursor state, raw response). This does not constitute an implicit semantic dependency of the DTO. Once produced, the DTO is semantically self-contained: no downstream stage requires knowledge of how or from where the DTO was extracted to perform its function. Provenance (how the DTO was selected) and meaning (what the DTO specifies) are orthogonal properties. Invariant #9 governs the latter.

---

## Completed (18 items)

### P0 — Core Stability
- ✅ T-02: Atomic Rollback Validation (#81)
- ✅ T-01: Path Traversal Hardening (#85)
- ✅ T-07: Exception Hierarchy + Circuit Breaker (#71, #87, #90)
- ✅ Issue A: Structured Contract Parsing (parse_llm_response)
- ✅ DOC-01: Consolidate adversarial session documentation

### P1 — Input Contracts
- ✅ Issue B: Issue Contracts (`--issue-file`) (#92)

### P2 Entry — Schema Versioning
- ✅ ADR-01/1: Write ADR-0004: Schema Versioning Policy (#73)
- ✅ ADR-01/2: Add `schema_version` to `RunMetadata` (#75)
- ✅ ADR-01/3: Version Guard at Pipeline Load Point (#77)

### P2 — Dogfooding & Hardening
- ✅ Experiment Artifacts Schema — Verdict + write_verdict utility (#79)
- ✅ Experiment 001 — First successful self-modification workflow
- ✅ Experiment 002 — Move write_verdict() to workspace.py
- ✅ Experiment 003 — Add `--risk-budget` flag to scan
- ✅ Issue #98 — Executor DAG Scheduler
- ✅ Issue #140 — Core Persistence (WAL atomic writes)
- ✅ Issue #142 — Post-Audit Remaining Fixes
- ✅ Issue #145 — Hardening Sprint (#146)
- ✅ Issue #149 — Workspace Hash Inconsistency (#150)
- ✅ Issue #151 — Validator timeout config + feedback (#151)
- ✅ Issue #153 — Force provider override observability (#154)
- ✅ Issue #156 — Literal validation + code-gen risk floor (#157)
- ✅ Issue #155 — Validator portability: venv PATH + ignore_dirs forwarding (#158)
- ✅ Issue #159 — Fix empty `patch.diff` on re-execution of `preview` (#160)
- ✅ Exp 006 — `safety.py` docstrings (#161)
- ✅ Formalize Experiment Schema (debt P2→P3)

### P3 — Async Workers & CI/CD Integration
- ✅ B6 — Risk Gate Audit Trail (#118)
- ✅ B1 — WAL Atomic Apply (#121)
- ✅ B2 — RunMetadata SSoT (#123)
- ✅ B4 — CB Externalized (SQLite) (#126)
- ✅ B7 — Workspace Isolation + Repo Lock (#128)
- ✅ B8a — Work Queue Schema (#132)
- ✅ B5 — Artifact Store (#134)
- ✅ B3 — GitHub Client (#136)
- ✅ B8b — Worker Loop (#138)
- ✅ Post-Audit Fixes — Path traversal validation, atomic artifact writes, lock failure logging (#164/#166/#167)
- ✅ Issue #162 — Replace Groq with OpenRouter (provider hardening)
- ✅ Issue #171 — GitHub Actions pipeline workflow (CI/CD integration)
- ✅ Issue #176 — Provider fallback chain for architect, scout, and validator summarizer (#177)

**P3 closure items remaining:** None — all P3 items complete.

**Recent:**
- ✅ D-006 — Executor pre-diff syntax validation (PR #202, 2026-07-08): new `validation.py` module with `validate_python_content()` using `ast.parse()`. Rejects non-Python LLM output (XML markup, prose) before diff/staging for `.py` files. Only rejects when original parses but modified does not (no false positives on pre-existing syntax errors). Non-`.py` files skip validation. 7 new tests.
- ✅ Dogfooding 006 — D-001 validated end-to-end (PR #201, 2026-07-08): `[TARGET FILES]` injection into architect prompt prevents phantom paths. D-005 (bad function boundaries) and D-006 (executor writes markup) documented.
- ✅ Issue #198 — Asymmetric risk gates + D-004 timeout bump (PR #199, 2026-07-07): `auto_apply_eligible` informational field on `RunMetadata`, `DEFAULT_TIMEOUT` 300→450s. `compute_auto_apply_eligible()` extracted to shared function in `artifacts.py`.

**Completed bug fixes from Dogfooding 002:**
- ✅ **CRLF fix (Issue #192)** — Added `newline=""` to all write paths: `local_store.py`, `work_queue.py` (×2), `__init__.py` (`_wal_write`). Regression + idempotency tests added.
- ✅ **Git root mismatch audit** — `apply.py` uses `git -C target_path` throughout; `validation_workspace.py` creates fresh `git init`. PatchForge commands are protected. Risk only if user runs `git apply` manually from git root.

**P3 closure items complete:**
- ✅ Issue #181 — Docker containerization (PR #182, 2026-06-29)
- ✅ Issue #183 — CI/CD reusable workflow + `patchforge ci` command (2026-06-30)
- ✅ Hardening — CI coverage collection + ruff B/SIM/C4 rules (PR #186, 2026-07-02, closed)
- ✅ Hardening — CI coverage split: separate data collection from report generation (PR #187, 2026-07-02)
- ✅ Hardening — Direct tests for preview.execute(): 8 scenarios + 2 safety invariants (PR #188, 2026-07-02)
- ✅ Dogfooding 002 — Portfolio backend (2026-07-02): PhilosophyItemSchema Field() metadata. Pipeline reliability FAIL (CRLF bug, Windows). Patch quality PASS (correct semantics, QA green after manual apply). 2 bugs discovered: CRLF in patch.diff, git subdirectory root mismatch.
- ✅ Dogfooding 003 — Portfolio backend CRLF fix E2E validation (2026-07-02): `status=previewed`, `overall_passed=true`, `patch.diff` LF-only. CRLF fix confirmed end-to-end on Windows.
- ✅ Dogfooding 004 — PatchForge self-modification (`_is_dangerous` requirements* variants, 2026-07-02): code change correct. 3 silent-failure findings: D-001 (phantom path in plan), D-002 (timeout 120s too short), D-003 (executor ERROR masked as previewed).
- ✅ Issue #194 — Silent-failure hardening (D-001/D-002/D-003): `validate_plan_paths()` new module, `DEFAULT_TIMEOUT` 120→300s, `executor_had_errors` field + `validation_failed` on hard errors (2026-07-07)
- ✅ Dogfooding 005 — E2E verification of D-001/D-002/D-003 fixes (2026-07-07): D-003 fully verified (executor_had_errors, validation_failed, apply blocked). D-001 not exercised (known recall limitation). D-002 partial (300s still insufficient for 633-test suite). New finding D-004.
- ✅ D-001 root cause fix — Inject target file listing into Architect prompt (2026-07-07): new `file_collector` module lists all target files (no extension filter), injects `[TARGET FILES]` block + path constraint instruction into both `ARCHITECT_PROMPT` and `ISSUE_ARCHITECT_PROMPT`. Cap at 500 paths with truncation. `validate_plan_paths()` remains as defense in depth.
- ✅ D-006 fix — Executor pre-diff syntax validation (PR #202, 2026-07-08): `ast.parse()` rejects non-Python LLM output before staging. Gated on `.py` extension; false-positive guard for pre-existing syntax errors. 7 regression tests.

---

## CI/CD Pipeline (P3)

PatchForge ships a reusable GitHub Actions workflow (`workflow_call`) that any repo can consume with a 10-line caller workflow. Pipeline execution runs inside Docker; GitHub API calls (push, PR, labels) run on the runner.

**Architecture:** Container/runner boundary separation:
- **Container** (`patchforge ci`): scan → plan → preview → apply → commit (no push, no GitHub API)
- **Runner**: git push, `gh pr create`, issue comments/labels, artifact upload

**CLI:** `patchforge ci <path> --workspace <dir>` — full pipeline, writes `ci_result.json`

**Workflow files:**
- `.github/workflows/patchforge-pipeline.yml` — reusable `workflow_call` workflow
- `.github/workflows/patchforge-on-label.yml` — thin caller for PatchForge's own repo

**Setup guide:** `docs/ci-cd-setup.md`

**Required repository secrets (at least one):**
- `ANTHROPIC_API_KEY` (recommended — Claude for architect)
- `GOOGLE_API_KEY` (Gemini fallback for low/medium risk)
- `OPENROUTER_API_KEY` (multi-model routing)

**Artifacts:** Each run uploads `runs/<run_id>/` as workflow artifacts (30-day retention). Contains `run.json`, `findings.json`, `plan.json`, `patch.diff`, `validation.json`, `apply.json`.

**Labels:**
- `patchforge/process` — triggers pipeline execution
- `patchforge/completed` — set on success (replaces `patchforge/process`)
- `patchforge/failed` — set on failure (replaces `patchforge/process`); a comment with the failure link is posted

---

## Document Map

| Document | Purpose |
|----------|---------|
| `docs/context/Workflow.md` | Daily workflow, QA gates, branch naming, commit format, AI roles |
| `docs/context/reference.md` | Known technical debt, failed approaches, open design questions, QA history |
| `docs/context/discoveries.md` | Technical debt discovered during implementation |
| `docs/planning/issue-registry.md` | Full issue inventory (P0–P5) with status, scope, and acceptance criteria |
| `docs/planning/roadmap-phase2.md` | Strategic roadmap, priority rationale, dependency chain |
| `docs/product-thesis-v2.md` | Product definition, non-goals, artifact contract |

For ADR records, see `docs/adr/`.
