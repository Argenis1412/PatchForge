# PatchForge — Project Context

> Last updated: 2026-07-17
> This document is the single source of truth for AI sessions. Read before any implementation work.

---

## Project at a Glance

**What:** PatchForge — AI-powered, safety-first code modification tool. Generates, validates, and applies patches through a deterministic Plan → Preview → Validate → Apply pipeline.

**Phase:** P4 — Trust & Configuration (complete; P0/P1/P2/P3 also complete)

**Stack:** Python 3.12+ | Pydantic schemas | Typer CLI | ruff + pytest QA

**CLI:** `patchforge` (primary), `orchestrator` (legacy alias)

**QA:** `pytest` → 883 passed, 5 skipped | `ruff check .` → 0 errors | `ruff format --check` → clean

**Key constraint:** Single-threaded, synchronous pipeline (invariant; Docker containerization complete in P3). `SqliteCircuitBreakerStore` is now thread-safe (issue #219).

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
├── paths.py               # Centralized PROJECT_ROOT resolution
├── plan_validation.py     # Filesystem path validation for plans (D-001)
├── risk.py                # Plan gate + patch gate + parse_diff_files
├── safety.py              # Path-safety validation utilities
├── validation_workspace.py
└── workspace.py           # WorkspaceManager — disk layout

tests/                     (27 test files, 695+ tests)
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
| 223 | Validator: resolve ruff/pytest via `sys.executable -m` | Fixes PATH lookup failure on Windows without `.venv` |

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

## Completed (20 items)

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
- ✅ Issue #223 — Validator PATH resolution via `sys.executable -m` (#224)

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

### P4 — Trust & Configuration
- ✅ Issue #226 — Qualitative Risk Gates (2026-07-12): file-semantic taxonomy in `check_plan_gate()` classifies
  files by path-prefix (`schemas/*` = HIGH, `tests/*` = LOW, etc.) as additive layer on `DANGEROUS_PATTERNS`.
  Taxonomy tiers only escalate (never downgrade). Audit trail reasons include escalation source. 18 new tests.
- ✅ Issue #228 — IssueContract ADR (2026-07-13): ADR-0005 defines `IssueContract`, the canonical
  source-agnostic issue schema for future GitHub API and Scout sources. Coexists with `IssueInput`
  (no adapter/pipeline wiring). `extra="forbid"`, no origin discriminator or free-form metadata dict,
  no defaults on semantic fields, `schema_version` deferred as Known Debt. 6 new tests.
- ✅ Issue #230 — Provider Registry (2026-07-13): `ProvidersConfig`/`ProviderModelConfig`
  (`extra="forbid"`) let `orchestrator.json` override the `gemini`/`openrouter`/`claude` models used by
  `providers.py`, with the hardcoded constants as fallback. `init_provider_models(config)` resolves once
  per run (called from both `executor.run()` and `validator.run()`); `_get_model(name)` reads the cache.
  `exec_meta` gains `models_resolved` (audit) and `cost_llm` (null only when Claude was both overridden
  away from the cost table's model AND actually used in the run — not merely because config has an
  override). `RunMetadata.provider_config` (previously unused) is populated for audit/worker cold-start.
  `FileChange.provider_name` is additive. No changes to fallback chain logic or risk-level routing. 11 new tests.
- ✅ Issue #232 — Audit Bundle Export (2026-07-15): `patchforge export-audit <run_id>` produces
  `audit-<run_id>.tar.gz` — a SHA-256-manifested tarball mirroring `runs/<run_id>/` under `artifacts/`,
  plus `manifest.json` (PatchForge version, `commit_anchor`, UTC timestamp, full `RunMetadata` structural
  mirror). Terminal-state gate is `status in {applied, failed, validation_failed}`; a residual `*.wal`
  sidecar is treated as an interrupted run. Optional `--sign`/`--gpg-key` produce a detached armored
  signature. `patchforge verify-audit <bundle>` reads the tarball entirely in memory (no `extractall`,
  no temp-dir extraction) to recompute hashes and detect both missing and injected/undeclared artifacts;
  `--require-signature` makes signature absence a verifier-side policy failure rather than an in-bundle
  claim an attacker could strip alongside the signature itself. New `schemas/audit_manifest.py`
  (`AuditManifest`, `ArtifactHash`, `extra="forbid"`) — a terminal derived artifact, not an inter-stage
  DTO. Zero changes to `pipeline.py` or `RunMetadata`. 34 new tests (3 GPG sign/verify tests skip without
  `gpg` on PATH; GPG failure paths and CLI flag passthrough are covered via mocked `subprocess.run`,
  needing no real `gpg`).
- ✅ Issue #234 — Redact sensitive fields from audit bundle export (2026-07-15): opt-in `--redact` flag
  on `export-audit` replaces `secrets_ref`, `env_file`, `workspace_path`, `target_path`, `staging_dir`,
  `logs_dir`, `provider_config` with `"[REDACTED]"` — only when set — in both `manifest.json`'s
  `run_metadata` and the raw `artifacts/run.json` file, so a `verify-audit` hash check still passes.
  Default export (no flag) is unchanged, preserving the structural-mirror mandate for composition with
  future Approval Provenance fields. An anti-rot test asserts every `RunMetadata` field is classified as
  redact-worthy or public, failing CI if a new field is added without classification. 6 new tests.
  Follow-up debts (no lock during export, GPG signer allowlist + untested real-binary CI path) evaluated
  during the same triage and deferred to #235/#236.
- ✅ Issue #235 — Repo lock around export-audit's read window (2026-07-16): opt-in `worker_id`/
  `coordination_db_dir` params on `export_audit()` hold a repo lock across the status check, file walk,
  and hash loop — the full TOCTOU-prone read window. Lock failure aborts with exit code 8 (new). Worker
  identity auto-generated via `uuid.uuid4().hex` (or `{worker_id}:export-audit` suffix when explicit) to
  prevent silent same-identity bypass and reentrant-release bugs. Metadata refreshed from locked read of
  `run.json` for manifest/tarball consistency. Supersedes the "no repo lock" non-goal from
  `docs/planning/p4/04-audit-bundle-export.md`. No CLI flags — infrastructure for future `work_queue.py`
  integration. 4 new tests.
- ✅ Issue #241 — Approval Provenance (2026-07-16, closes P4): `RunMetadata.triggered_by`/`approved_by`
  are additive fields (no `schema_version` bump) recording who ran a stage, not an authorization gate.
  `triggered_by` is captured in `scan`/`ci` from `GITHUB_ACTOR` when present, falling back to
  `git config user.name`/`user.email` locally; `ci.py`'s `_fail()` closure carries it so failed runs stay
  auditable, not just successful ones. `approved_by` is captured only in `apply.py` at the actual human
  gate — never at scan/ci time, when no approval has occurred yet. New `src/orchestrator/provenance.py`
  (Level-2 domain module: source selection, partial-identity fallback) and two `git.py` Level-1 wrappers
  (`git_config_user_name`/`_email`, degrading to `None` on unset config, timeout, or missing git binary).
  Both fields classified as public (not redacted) in `export_audit._PUBLIC_FIELDS`. Surfaced in PR bodies
  from both the worker-loop path (`work_queue.py`) and the GitHub Actions workflow. Planning doc's "Step 0"
  PR-body consolidation into a single choke point was scoped down — only 2 real call sites exist and the
  YAML workflow cannot call Python, so a full consolidation would have been over-engineering. 27 new tests.

### Planning
- ✅ Issue #221 — Post-P3 roadmap consolidation (2026-07-11): new `docs/planning/roadmap.md` (Core P4–P5 with agreed cuts + explicit Deferred section) and `docs/planning/scout-vision.md` (Scout frozen as second product line). Live docs (index, README, CLAUDE.md, CONTEXT.md, thesis) repointed; obsolete P3 sprint prompts and superseded roadmaps removed.
- ✅ P4 planning scaffold (2026-07-12): `docs/planning/p4/` created with README + 5 per-item docs (steps, difficulty, scope, branch/commit).

### Tech Debt Closure
- ✅ Issue #219 — CB thread-safety gaps pre-P3 (#219): `_sqlite_connect()` opt-in `check_same_thread=False` + `SqliteCircuitBreakerStore._conn_lock`; `_registry_lock` in `circuit_breaker_for()`; `_init_lock` in `providers._init_circuit_breakers()`. Lock ordering documented. 2 regression tests.
- ✅ Issue #212 — Close verified tech debt: 7 entries marked ✅, harden ci apply (`git add -A` → targeted staging via `parse_diff_files`), centralize `PROJECT_ROOT` in `orchestrator/paths.py`
- ✅ Issue #245 — Fence-stripping fallback for executor LLM output (2026-07-17, PR #247), discovered in Dogfooding-010: new `strip_fences()` in `validation.py`, called from `applier.py` right before `.py` syntax validation. Handles ``` and ~~~ fences (with/without language tags, including `c++`/`f#`/`objective-c`), preamble/trailing text, and preserves inner backticks — only strips when exactly one complete fence pair is found, so ambiguous content (unclosed, mismatched types, multiple pairs) is left untouched. Skips `.md`/`.markdown` files. `_strip_markdown()` in `providers.py` (naive, can corrupt inner-backtick content) is left as a documented known limitation, not fixed here. 16 new tests.
- ✅ Issue #246 — Provider Registry wired into architect and scout (2026-07-17, PR #248), discovered in Dogfooding-010: `init_provider_models(config)` now runs in both `architect.run()`/`run_from_issue()` and `scout.run()` before their first LLM call, so a model pinned in `orchestrator.json` finally affects `plan` and scout's reconnaissance passes — previously only `executor`/`validator` honored the registry. Local `_MODEL_MAP`/`_COST_RATES` in both agents were removed; `model_used` resolves via the shared `_get_model()`, and cost is taken directly from `_call_chain()` (nullable via `_compute_cost()`'s existing guard) instead of being recomputed against a stale rate table that silently produced wrong numbers on an override. `log_call` and console cost prints were made `None`-safe end-to-end. 10 new tests.
- ✅ Issue #256 — Module-probe cwd hardening against CWE-427 (2026-07-17): `tool_probe.py`'s `_probe_module()` no longer runs `sys.executable -m <tool> --version` from the shared OS temp dir (`_PROBE_CWD = Path(tempfile.gettempdir())`, world-writable, allowing a local co-tenant to shadow `ruff`/`pytest` and get code executed under the probing account). Replaced with a private per-probe `tempfile.TemporaryDirectory(prefix="probe_", ignore_cleanup_errors=True)` used as a context manager — creation and cleanup are both handled by the stdlib, closing the manual `mkdtemp`/`finally`/`rmtree` bug class an earlier draft had. New `tests/test_tool_probe.py` (4 tests) includes a CWE-427 regression test that plants a shadow module in a simulated shared temp dir (isolated via `monkeypatch`, never the real OS temp dir) and asserts it is never imported. Closes the discovery logged in #252/PR #253. Resolves the pre-existing risk originally flagged by CodeRabbit on PR #253.
- ✅ Issue #258 (Parts 1–4) — Resumable `apply`, split into sequential sub-issues per `docs/context/plan-issue-258-resumable-apply.md` to keep each PR independently reviewable (an earlier bundled draft of this work grew `apply.py` to ~1000 lines and hid several bugs until an after-the-fact review — see Scope Limits in `.claude/CLAUDE.md`):
  - **Part 1** (PR #259) — `PatchLifecycleState.ALREADY_APPLIED` detection: `apply` distinguishes "patch already present, matching HEAD, uncommitted" from a real CONFLICT.
  - **Part 2** (issue #260, PR #261) — Automatic resume from ALREADY_APPLIED when the original run started on a clean tree: WAL hydration (`_hydrate_apply_result_for_resume`), triple isolation check (HEAD/branch/residue), pre-apply `TargetConfig` snapshot to avoid re-loading config from the mutated tree, lock acquired before any HEAD read.
  - **Part 3** (issue #262, 2026-07-18) — `apply --allow-dirty` on a dirty tree now captures pre-existing tracked + untracked changes (`stash_create_dirt` in `git.py`, low-level plumbing since `git stash create --include-untracked` silently no-ops rather than raising) before mutating, and restores them both on rollback and on success (restoring only on rollback would have stranded the user's dirt in a stash forever on a successful apply — corrected during implementation). New `ApplyResult`/`RunMetadata` fields: `dirt_stash_sha`, `dirt_restored`, `dirt_restore_failed`, `dirt_recovery_command`. The ALREADY_APPLIED/resume path now aborts explicitly (rather than silently proceeding) if a WAL records an unrestored dirt capture — that contract with the not-yet-built Part 4 (dirt-aware resume) is documented in the plan doc. 11 new tests in `tests/test_apply_resumable.py`, including a mandatory structure test asserting the manually-built stash commit's parent count and that `git stash apply --index` accepts it (guards against relying on undocumented git internals).
  - **Part 3.5** (issue #264, PR #265, 2026-07-19) — Dirt-capture storage moved off `refs/stash`, discovered as a prerequisite during Part 4's planning adversarial review: `stash_drop`'s `stash@{0}` (positional, top-of-stack) addressing was a real TOCTOU risk once resume introduced an unbounded gap between pushing and dropping the entry — concurrent `git stash` activity by the user in that gap could shift the target and silently destroy an unrelated stash entry. Replaced with a private per-run ref, `refs/patchforge/dirt/{run_id}` (`store_dirt_ref`/`delete_dirt_ref`/`check_orphaned_dirt_refs`/`dirt_ref_name` in `git.py`, via `git update-ref`), addressed by exact name instead of position — eliminates the TOCTOU class entirely and stops polluting the user's own `git stash list`. `store_dirt_ref` is create-only (fails rather than overwriting a stale ref from an incomplete prior cleanup); `delete_dirt_ref` failure is non-fatal (the tree is already correct by that point — surfaced as a warning, left for the orphan advisory). The orphan-startup-advisory no longer needs name-collision suppression (the namespace is exclusively PatchForge's), and now reports every found orphan directly by `run_id` with an age-based (7-day, `run.json` mtime) manual-cleanup hint. Also added `has_merge_conflicts()` to distinguish a clean `stash_apply_dirt` failure from a partial 3-way-merge conflict in FATAL/warning messaging (pre-existing Part 3 ambiguity, fixed while this code was already being touched). **Known accepted risk, not fully eliminated:** anchoring via a git ref (any git ref, including the `refs/stash` this replaced) is still swept up by `git push --mirror`/wildcard-refspec pushes; a per-run namespace has a *worse* blast radius than the single-ref `refs/stash` tip it replaced, since orphaned captures from multiple crashed runs can accumulate — mitigated (not solved) by the age-based cleanup hint above; avoid `--mirror`/wildcard pushes while any `refs/patchforge/dirt/*` refs are outstanding. 8 tests in `tests/test_apply_resumable.py` updated/added (2 removed/rewritten — the old name-collision-suppression test's scenario is no longer reachable under the new design).
  - **Part 4** (issue #266, 2026-07-19) — Dirt-aware automatic resume, closing the Part 3/4 contract: `ALREADY_APPLIED` resume no longer aborts when a prior `--allow-dirty` run captured dirt. `lifecycle.py` untouched by design (dirt-agnostic classifier). Three sub-cases: (1) common case — the Part 3 hard-abort guard is removed and `wal_result.dirt_stash_sha` propagates into the resume path's local variable, activating the same shared restore blocks the happy path already used; (2) narrow crash window between a successful dirt restore and the final WAL write (classifier returns CONFLICT) — an independent `try_apply_dry_run_reverse` re-check selects an honest "check your tree first" message over the generic CONFLICT message, empirically bounded to the common case since `git stash apply --index` refuses per-file (not per-line) when dirt overlaps a file the patch also touches; (3) sub-case 0, found during adversarial review — a crash between the first WAL checkpoint and `apply_patch` leaves a clean tree the classifier sees as VALID, not ALREADY_APPLIED, so a naive retry would silently abandon the already-captured dirt ref. New `git.resolve_dirt_ref()` (read-only, fail-closed to `RuntimeError` — not `None` — on any git error other than "not a valid ref", so an undiagnosable state is never silently treated as absent) lets the happy path detect and reuse an orphaned ref for its own `run_id` before deciding whether to capture new dirt; a dirty tree at that point aborts before any mutation (old capture and new changes can't be merged automatically) with two explicit manual recovery paths. Orphan-advisory fixed to exclude the run being resumed in the same invocation, and to lead with "might be resumable" (not hide the warning — an early visibility-gating design was rejected in review, since no code path transitions an abandoned run out of `"applying"` status) for other refs whose WAL is still `"applying"`. 18 new/rewritten tests across `test_apply_resumable.py`/`test_lifecycle.py`, including 3 added during `/diff-review` to close coverage gaps the review pass found (an unwrapped `resolve_dirt_ref` call site, its `RuntimeError` path, and the SHA-mismatch abort). Full `/clarify` → `/challenge-ac` → `/adversarial` → `/diff-review` trail on issue #266.

**Recent:**
- ✅ Issue #223 — Validator PATH resolution via `sys.executable -m` (2026-07-12, PR #224): `run_ruff()`/`run_pytest()`
  defaults in `runners.py` changed from bare `["ruff", ...]`/`["pytest", ...]` to `[sys.executable, "-m", "ruff", ...]`/
  `[sys.executable, "-m", "pytest", ...]`. Fixes `VALIDATION_FAILED` false negatives on Windows clones without a
  `.venv`, where the system `PATH` lacks Python's `Scripts\` dir. `cmd_override` and existing venv auto-discovery
  (`_build_env_with_venv`, Issue #155) are unaffected — the guard now naturally skips venv-PATH injection since
  `sys.executable` is always absolute. Verified end-to-end via Dogfooding 009 (fresh clone, no `.venv`, Windows).
- ✅ Issue #210 — Executor new-file creation support (2026-07-09): `_apply_task()` no longer
  rejects files that don't exist on disk. When a file is missing from both the project root
  and staging, it sets `original_content=""`, uses a dedicated `_build_create_prompt()`, and
  generates `--- /dev/null` diffs via `_make_diff(is_new_file=True)` for `git apply`
  compatibility. Files already in staging from a prior task are treated as accumulated
  modifications. Syntax validation for new `.py` files is correctly enforced via the
  `"# new file\n"` stand-in passed to `validate_python_content()` (see corrected note
  in `discoveries.md`, Dogfooding 007). 5 new tests including `git apply --check`
  integration.
- ✅ Issue #208 — Executor observability + `ci --force-provider` (2026-07-09): `executor_agent.run()` now
  accepts `logs_dir`/`run_dir` and emits a full lifecycle event trail (`executor_start`, `task_start`,
  `file_start`/`file_end`, `task_end`, `task_skipped`, `executor_end`) via `log_event()`, wrapped in a
  `_safe_log_event` helper so observability failures cannot crash a run. `preview.py` and `pipeline.py`
  forward `logs_dir`/`run_dir` to the executor. `patchforge ci` gained `--force-provider` (shared
  `_validate_force_provider()` with `preview`), forwarded to the executor with a symmetric external
  `force_provider_override` event and recorded on `CiResult.force_provider`. Closes the audit-hole debt
  from issues #145 and #183 (documented in `discoveries.md`).
- ✅ Issue #205 — Add Claude as third fallback in validator summarizer (PR #206, 2026-07-08):
  extended `_call_chain([_call_openrouter], ...)` to `_call_chain([_call_openrouter, _call_claude], ...)`
  in `summarizer.py`. Fixed a model-tag misattribution found during adversarial review
  (`chain_result.provider_name` instead of a hardcoded `"openrouter/free"`). Resolves D-007b —
  the minimal one-argument extension was applied directly, without reusing `_cb_validator`
  for Claude.
- ✅ Dogfooding 007 — D-005 + D-006 validated end-to-end (2026-07-08): D-005 confirmed (architect correctly targeted `summarizer.py` from annotations; `_summarize_errors()` annotation was the decisive signal). D-006 happy path confirmed (T1 diff generated, modified `.py` syntactically valid). `validation_failed` due to LLM over-engineering Claude fallback (reused `_cb_validator` instead of extending `_call_chain`). Two new discoveries: D-007a (executor cannot create new files, confirmed again) and D-007b (LLM adds new try/except instead of extending existing `_call_chain` call; AC must name the construct to modify).
- ✅ D-005 — Architect structural context annotations (2026-07-08): `build_target_files_block()` annotates `.py` files inside Python packages with module docstring + top-level symbols via `ast.parse()`. Package detection runs before path truncation. 10k-char annotation budget. Graceful degradation on errors. 17 new tests.
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
| `docs/planning/roadmap.md` | Live PatchForge Core roadmap (P4–P5) with strategic rationale |
| `docs/planning/p4/` | Per-item planning docs for P4 (scope, difficulty, steps, branch/commit) |
| `docs/planning/scout-vision.md` | Long-term vision for Scout as a second product line (frozen) |
| `docs/product-thesis-v2.md` | Product definition, non-goals, artifact contract |

For ADR records, see `docs/adr/`.
