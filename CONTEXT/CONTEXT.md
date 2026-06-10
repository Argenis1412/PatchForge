# PatchForge — Project Context

> Last updated: 2026-06-10 | Session: Post-adversarial (27 attacks) + Workflow.md English translation
> This document is the single source of truth for AI sessions. Read before any implementation work.

---

## Working Style

10-step workflow per issue:

1. Read and understand the issue
2. Run **AI: Issue Clarifier** — find undefined terms, edge cases, unmapped interactions, ambiguities
3. Update the issue if gaps are found
4. Define acceptance criteria + **Exit Conditions** + **Out of Scope by Construction**
5. Run **AI: AC Challenger** — find untestable criteria, missing edge cases, redundancies
6. Create implementation plan with **Modification Budget**
7. Run **AI: Adversarial Reviewer** — challenge assumptions, find pre-existing solutions, unmapped interactions, silent bugs
8. Wait for approval
9. Create branch (`<type>/issue-<N>-<slug>`)
10. Implement → **AI: Diff Reviewer** → Add/update tests → QA (`ruff check` · `ruff format --check` · `pytest`) → atomic commit → push → PR

**Core rule:** The refactor is never the unit of work. The unit of work is a self-contained issue with limited scope and verifiable criteria.

**Risk distribution:** Each AI has a distinct responsibility (Clarifier, AC Challenger, Adversarial Reviewer, Diff Reviewer). No single AI has authority to design the whole solution.

**Rules:**
- Stop and ask if anything is ambiguous
- Implement only what the issue requires
- No unrelated refactors, no speculative improvements
- Keep diffs minimal
- Code, comments, commits, and PRs in **English only**
- Conventional commits only (`feat`, `fix`, `docs`, `refactor`, `chore`)
- Behavior changes require tests
- GPG-verified commits
- Golden Rule: Implement the smallest correct change that satisfies all acceptance criteria

---

## Repository Structure

```
src/orchestrator/          (41 Python files)
├── agents/
│   ├── architect.py       # Claude Sonnet 4.6 — generates task plan
│   ├── executor.py        # Multi-LLM routing — applies changes
│   ├── scout.py           # Gemini — repository analysis (legacy, AI)
│   └── validator.py       # subprocess — ruff + pytest
├── clients/
│   ├── anthropic_client.py
│   ├── gemini_client.py
│   ├── groq_client.py
│   └── bootstrap.py
├── commands/
│   ├── scan.py            # V1 deterministic scan (non-AI)
│   ├── plan.py            # V1 AI-assisted planning
│   └── preview.py         # Patch preview + validation
├── observability/
│   ├── events.py
│   └── logging.py
├── scanners/
│   └── python.py          # V1 deterministic Python scanner
├── schemas/
│   ├── architect_output.py
│   ├── artifacts.py       # RunMetadata — source of truth for run state (within a single execution under consistent schema version; no cross-version compatibility guarantee)
│   ├── config.py          # TargetConfig + TargetCapabilities
│   ├── executor_output.py
│   ├── findings.py        # ScanFindings — V1 deterministic scan schema
│   ├── git.py              # GitCommandResult, ValidationWorkspace, etc.
│   ├── pipeline_run.py
│   ├── risk.py
│   ├── scout_output.py
│   └── validator_output.py
├── doctor.py              # V1 readiness check
├── git.py                 # Pure git wrappers (no domain logic)
├── lifecycle.py           # Patch lifecycle state machine
├── main.py                # CLI surface (551 lines, known god object)
├── pipeline.py            # Central orchestrator (Pipeline class)
├── risk.py                # Plan gate + patch gate logic
├── validation_workspace.py
└── workspace.py           # WorkspaceManager — disk layout

tests/                     (20 test files, 208 tests)
```

---

## Current State

| # | Title | Date |
|---|-------|------|
| 59 | Fix remaining ruff violations (N818, E501, E402, I001) | Jun 8 |
| 57 | Update documentation to use `patchforge` CLI | Jun 8 |
| 55 | Rename CLI from `orchestrator` to `patchforge` | Jun 8 |
| 53 | Deprecate and hide legacy `run` command | Jun 8 |
| 51 | Fix: conditional preview status and pre-apply validation gate | Jun 7 |
| 49 | Implement `plan run_id` with bounded AI-assisted planning | Jun 7 |
| 45 | Replace AI Scout behavior with deterministic V1 scan | Jun 7 |
| 9c | Fix: block REBASEABLE patches from being applied in V1 | Jun 7 |
| 9b | Patch lifecycle states for V1 | Jun 6 |
| 41 | V1 risk gates and patch size limits | Jun 6 |
| 39 | Failure-state handling and apply rollback | Jun 6 |
| 37 | Fix architect: deprecated model + harden JSON parsing | Jun 6 |
| 31 | Doctor docstrings: edge cases + return value docs | Jun 5 |
| 33 | Doctor docstrings: all functions | Jun 5 |
| 30 | Doctor: TypeScript out-of-scope warning | Jun 5 |
| 28 | API key warnings | Jun 5 |
| 26 | Deterministic doctor command + V1 support gate | Jun 5 |
| 25 | V1 run-centric artifact persistence and commands | Jun 4 |
| 24 | Translate all Spanish to English | Jun 4 |
| 21 | Deterministic Git safety primitives + V1 isolated validation | Jun 3 |
| 20 | Enforce external workspace safety | Jun 2 |
| 18 | PatchForge thesis (initial scaffold) | May 27 |

### QA Metrics

| Check | Result |
|-------|--------|
| `pytest` | **208 collected, 207 passed, 1 skipped** |
| `ruff check .` | **0 errors** — clean across all files |
| `ruff format --check` | **Clean** (61 files formatted) |

### V1 Complete

**16/16 V1 issues implemented.** CLI: `patchforge` (primary), `orchestrator` (legacy alias).

| Command | Status | Description |
|---------|--------|-------------|
| `doctor` | ✅ | Readiness check (no AI, no modifications) |
| `scan` | ✅ | Deterministic repository analysis (no AI) |
| `plan` | ✅ | AI-assisted (Claude) — bounded by risk gate |
| `preview` | ✅ | Patch generation + validation |
| `apply` | ✅ | Git-safe patch application |
| `run` | ❌ deprecated | Stub with warning, hidden from help |

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

## Next Task

*To be determined.*

---

For reference materials (known technical debt, failed approaches, design questions, QA history), see `CONTEXT/reference.md`.

For technical debt discovered during implementation, see `CONTEXT/discoveries.md`.

For the product thesis (product definition, non-goals, artifact contract), see `docs/product-thesis-v2.md`.
