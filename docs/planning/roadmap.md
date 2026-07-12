# PatchForge — Roadmap

> **Date:** 2026-07-11
> **Status:** Live. Supersedes `roadmap-phase2.md` and the V1 `ROADMAP.md`.
> **Scope:** PatchForge Core only. Scout is a separate product line — see `scout-vision.md`.

---

## North Star

PatchForge is a **trust interface between a human problem and an AI-generated solution.** Not an autocomplete. Not an autonomous agent. A verifiable pipeline that accepts a structured specification and produces a diff validated against real tests, reviewed by a human before any file is modified.

The product is not generated code. The product is **demonstrable trust.**

For the full thesis and architectural invariants see `docs/product-thesis-v2.md` and `docs/context/CONTEXT.md`.

---

## Two Product Lines

Post-P3 we recognize two distinct products that share the pipeline but answer different questions:

- **PatchForge Core (this roadmap)** — *Execute changes safely.* Planner, Preview, Apply, Validator, async workers, CI/CD, risk gates, audit bundles, provider registry, ledger.
- **PatchForge Scout (frozen — see `scout-vision.md`)** — *Discover what changes are worth executing.* Detection wrappers, Issue Registry, ranking, file-level correlation, intelligence.

**Rule:** Scout ideas surfacing during P4/P5 go to `scout-vision.md`'s backlog. They never open Core issues.

**Sequence commitment:** Core reaches 2.x → `IssueContract` published as a stable public interface → Scout starts in a separate repo → Scout integrates as an *optional* input source.

---

## Design Pattern: Determinism First, AI Interprets

The strongest Core ideas share a shape: **deterministic infrastructure with LLMs reserved for interpretation, never for control flow.** Provider Registry, Ledger, Audit Bundle, Approval Provenance, Test Selection — none require an LLM. Risk Gates use LLMs minimally. Only Feedback Loop and AC Compiler admit LLM judgment, and both are structured so a deterministic path always exists.

This is not a preference — it's how PatchForge stays auditable at enterprise scale.

---

## Status Snapshot

- **V1** — Complete. Deterministic CLI pipeline: doctor, scan, plan, preview, apply.
- **P0** — Complete. Core stability (atomic rollback, path hardening, exception hierarchy, structured contract parsing).
- **P1** — Complete. Issue Contracts via `--issue-file`.
- **P2** — Complete. Schema versioning (ADR-0004), dogfooding experiments 001–008, hardening sprint, DAG scheduler, WAL persistence.
- **P3** — Complete. Async workers, CI/CD, Docker, GitHub Client, Work Queue, Artifact Store, externalized Circuit Breaker, worker loop, thread-safety hardening (#219).

Full issue inventory in `docs/planning/issue-registry.md`. Full timeline in `docs/context/CONTEXT.md`.

---

## P4 — Trust & Configuration

**Theme:** Make the pipeline configurable and auditable enough for enterprise B2B (banking, healthcare, government). This is where the "trust layer" thesis becomes commercial.

**Order of implementation:**

### 1. Qualitative Risk Gates (idea 2)
- **Goal:** Extend `check_plan_gate()` with a file-semantic taxonomy (`schemas/*` = HIGH, `tests/*` = LOW, etc.) so `auto_apply_eligible` uses richer criteria than DANGEROUS_PATTERNS.
- **Impact:** High. Closes the gap between "counting diff lines" and "understanding what is being touched."
- **Effort:** 3–5 days.
- **Risk:** Touches `risk.py` (business logic) but not `pipeline.py`. No ADR needed.
- **Cuts:** Backward compatible — no taxonomy in config = current behavior byte-identical.

### 2. IssueContract ADR (idea 6)
- **Goal:** ADR-0005 + schema in `schemas/issue.py` defining `IssueContract` as the canonical issue representation across all three sources (human markdown, GitHub API, future Scout).
- **Impact:** Medium immediate, high strategic. Prevents a costly refactor when Scout arrives.
- **Effort:** 2–3 days. ADR + schema + round-trip test. Zero pipeline code.
- **Risk:** Low. No consumers change in this issue.
- **Cuts:** Adapter for GitHub Issue → IssueContract is a separate future issue.

### 3. Provider Registry (idea 9)
- **Goal:** Make the models in `providers.py` configurable via a `providers` section in `orchestrator.json`, with current constants as defaults.
- **Impact:** Medium-high. Adoption barrier: enterprise users on Azure/Bedrock and users wanting non-free OpenRouter models must edit source today.
- **Effort:** 2–4 days.
- **Risk:** Config schema extension; no `pipeline.py` change. No ADR.
- **Cuts:** Model field only (no custom endpoints, no plugin providers). Override of Claude records `cost_llm: null` + warning rather than a wrong number.

### 4. Audit Bundle Export (idea 7)
- **Goal:** `patchforge export-audit <run_id>` → tarball + manifest with SHA-256 of every artifact + PatchForge version + `schema_version` + providers used + `commit_anchor`. Optional GPG signing.
- **Impact:** High for enterprise. Nobody in the space produces this. Turns the "auditable artifacts" property into a compliance-grade deliverable.
- **Effort:** 3–5 days.
- **Risk:** New CLI command in `commands/`. No invariants touched.
- **Cuts:** Local export only (no S3, no artifact registries, no RFC 3161 timestamping).

### 5. Approval Provenance (idea 10)
- **Goal:** Two additive `RunMetadata` fields — `triggered_by` and `approved_by` — captured from `github.actor` in CI and `git config user.*` locally. PR body includes the provenance line.
- **Impact:** Medium-high for enterprise (separation of duties). Formalizes the human gate the thesis already requires.
- **Effort:** 3–5 days.
- **Risk:** Additive with default (no schema-version bump per ADR-0004). Reinforces the thesis rather than modifying it.
- **Cuts:** Record only, not policy. Authorization (who *may* approve) is GitHub branch protection / CODEOWNERS territory.

---

## P5 — Learning Pipeline

**Theme:** Turn the pipeline into a system that accumulates knowledge across runs. Every run becomes a data point; every failure becomes a classified signal. Faster feedback, less friction, richer diagnosis.

**Order of implementation:**

### 1. Experiment Ledger (idea 4)
- **Goal:** Persist an `ExperimentRecord` per run in `experiments.db` (SQLite, using `_sqlite_connect()`). `patchforge stats [--last N]` reports success rate, top failure types, avg cost, avg duration.
- **Impact:** Medium-high. Today 8 dogfoodings → zero aggregate data queryable.
- **Effort:** 3–5 days.
- **Risk:** Low. Established SQLite pattern from P3. Append-only.
- **Cuts:** Ledger, not analytics engine. No dashboards, no external metric services. Consumes `failure_types` from Feedback Loop when it lands; before that, the field is empty.

### 2. Impacted-Test Selection (idea 8)
- **Goal:** Two-level validation. `preview --fast-validation` runs only tests importing the changed files (via reverse import graph). `apply` and `ci` always run the full suite.
- **Impact:** High friction reduction. Preview cost for a 1-file change: ~150s → <30s.
- **Effort:** 1–2 weeks.
- **Risk:** Highest of P5. The full suite must remain the gate for anything ending in apply/PR (thesis constraint). Mitigation: pre-filter opt-in only.
- **Cuts:** Deterministic import graph only (no LLM, no coverage history, no ML). Conservative degradation: touched conftest / dynamic imports / non-Python files → automatic full suite.

### 3. Executor Feedback Loop (idea 3)
- **Goal:** `ExecutorDiagnosis` DTO per failed task, classifying the failure into a typed enum. V1 emits deterministic types only.
- **Impact:** High. 6 of 8 dogfoodings had `validation_failed` or `executor_had_errors` with zero classification.
- **Effort:** 1–2 weeks.
- **Risk:** New inter-stage schema (Invariant #9 + round-trip). No ADR (schemas already extend regularly).
- **Cuts:** Enum defined with full 6 values, but V1 emits only 3 deterministic (`SYNTAX_INVALID`, `FILE_NOT_FOUND`, `PROVIDER_UNAVAILABLE`) + `UNCLASSIFIED` for the rest. No `suggested_ac_refinement` field. LLM classifier is a future extension.

### 4. AC Compiler (idea 5)
- **Goal:** Enrich `IssueInput` ACs with file/symbol anchors resolved deterministically via `ast.parse()` and symbol search, producing a `CompiledIssue` that *composes* `IssueContract`.
- **Impact:** High. AC vagueness is the most repeated cause of implementation errors in dogfooding.
- **Effort:** 1–2 weeks.
- **Risk:** Highest of the batch. Sits on the frontier `schemas/issue.py` ↔ `agents/architect/`. Overlap with existing `file_collector` needs mapping first.
- **Cuts:** Deterministic-only in V1. Only anchors ACs that literally name a symbol or path. Semantic prose→construct mapping is Scout territory — not "future extension." No auto-generated DO-NOT constraints.

---

## Deferred (with explicit conditions)

- **TS Support (idea 11).** Spike first: 1–2 page inventory of every point in the pipeline that assumes Python + one manual E2E run against a small TS repo via `--issue-file`. Go/no-go based on the spike, not intuition. Only expands to another language when Core is stable; Scan stays Python-only regardless.
- **Defense in Depth (auto-seeding characterization tests, shadow patching).** Preserved from P2's original condition: implement only after dogfooding of a real test-zero legacy repo reveals concrete failure modes. Not driven by intuition.
- **Experiment Framework & Metrics — analytics half.** The Ledger (P5-1) delivers the persistence and CLI reporting half. The remaining half (trend tracking, prediction, dashboards) stays deferred here as its own line item — not silently absorbed by the Ledger.

---

## Scope Discipline

- **Not on any roadmap:** Autonomous apply without human review; monolithic `run` command; free-text issue interpretation without structured contracts; cross-version artifact compatibility without ADR. These are non-goals per thesis §4.
- **Scout ideas that surface during P4/P5:** logged to `scout-vision.md` backlog immediately. Never become Core issues.
- **Ideas discovered during dogfooding that don't fit this roadmap:** logged in `docs/context/discoveries.md`, converted to issues only when scope is proven.
