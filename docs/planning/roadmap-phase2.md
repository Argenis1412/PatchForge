# Phase 2 & Dogfooding Roadmap

This document outlines the sequence of implementation for the Phase 2 Blockers and the subsequent transition to self-improvement (dogfooding), based on the strategic analysis of PatchForge's market positioning as the **trust layer** for enterprise AI adoption.

---

## Strategic Positioning

PatchForge's true market value is in **enterprise B2B**: banks, healthcare, government, and critical codebases where "modify and pray" is unacceptable. The key differentiators are:

- **Auditable changes:** Every run produces `patch.diff`, `run.json`, `plan.json`, `validation.json` — gold for regulated environments.
- **Never-modify-first pipeline:** Plan → Preview → Validate → Approve → Apply mitigates financial and operational risk.
- **Git-native, platform-independent:** Works on-premise, air-gapped, with GitLab, Bitbucket, or GitHub. No cloud lock-in.
- **Hybrid cost architecture:** Deterministic scanning ($0) for navigation; LLMs reserved for architecture and refactoring judgment. Commercially viable at scale.

### Key Challenges

1. **"Blindness" on test-zero codebases:** PatchForge's trust layer relies on automated validation (pytest, ruff). Legacy repos with 0% coverage reduce the system's ability to demonstrate safety.
2. **Developer friction:** The rigorous plan → preview → validate → apply flow is safe but slow. Developers are impatient — removing friction is critical for adoption.

### Priority Decision: Async Workers First

When choosing between **Defense in Depth** (solving test-zero repos) and **Async Workers** (reducing friction), the decision falls on **Async Workers** for four reasons:

- **Lower risk and complexity:** Docker + CI/CD + auto-PR is primarily infrastructure. It requires no new AI logic, no pipeline changes, no new patch lifecycle states.
- **ROI requires evidence first:** Shadow patching and auto-seeding tests are elegant but expensive — and their ROI is zero until dogfooding reveals real legacy repos that break the current pipeline.
- **Friction is the immediate bottleneck:** The current sequential terminal flow demands sustained developer attention. Async workers convert that into "open an Issue → receive a PR" — eliminating fatigue without compromising structure.
- **Connects naturally to Risk Gates:** Asymmetric risk gates (low-risk → auto-PR, high-risk → manual review) are already sketched in the roadmap. Workers + gates create a coherent flow.

---

## Implementation Priority

### P0 — Core Stability (Phase 2 Blockers)
These must be resolved before any self-improvement experiments begin.

1. ✅ **T-02: Atomic Rollback Validation**
   - Goal: Implement a reliable rollback primitive for the Executor to ensure the repository returns to a clean state upon failure.
2. **T-01: Path Traversal Hardening**
   - Goal: Enforce strict path construction contracts to prevent directory traversal attacks and workspace leakage.
3. **T-07: Exception Hierarchy**
   - Goal: Replace generic `RuntimeError` with typed exceptions (`PatchForgeError` base) and implement a circuit breaker for provider failures.
4. **T-03: Structured Contract Parsing**
   - Goal: Replace fragile `_extract_json()` with a robust, schema-aware parser that converts LLM output directly into Pydantic models.

### P1 — Input Contracts
- **Issue A: Structured Contract Parsing Foundation**
  - Implementation of the generic `parse_llm_response` utility.
- **Issue B: Issue Contracts (`--issue-file`)**
  - Enable the pipeline to consume human-written markdown issues as the primary source of truth.

### P2 — Experimentation Infrastructure & Dogfooding

**P2 entry condition (ADR-01):** Schema versioning policy must be resolved before P2 begins. Dogfooding produces experiment artifacts (`plan.json`, `run.json`, etc.) that will be compared, reviewed, and potentially reprocessed across software versions. The current architecture relies on a "single pipeline run" scope restriction (Invariant #3) that does not extend to cross-version compatibility. ADR-01 must define the schema version identity mechanism before artifacts become persistent records with cross-version lifespans.

- **Experiment Artifacts**
  - Implementation of a structured record for every run (`issue.md`, `plan.json`, `patch.diff`, `qa_logs`, `verdict.md`).
- **Experiment 001 (POC)**
  - First controlled run: Rename `_extract_json` to `_parse_llm_json` using the clone method.
  - Purpose: Generate empirical evidence before investing in Defense in Depth.

### P3 — Async Workers & CI/CD Integration (Friction Reduction)
- **Goal:** Decouple the developer from the critical path. Transform the pipeline from synchronous CLI into an async CI/CD workflow.
- **Docker containerization:** Package the core (orchestration, git wrappers, schema validation) as a standalone container, isolated from the CLI entry point.
- **CI/CD integration:** GitHub Actions / GitLab CI worker that listens for Issues, clones the repo, executes plan → preview → validate, and opens a Pull Request with structured artifacts.
- **Asymmetric risk gates (light version):** Low-risk changes (`.md`, templates) → auto-PR; high-risk changes (schemas, core logic) → requires manual approval.
- **Developer workflow:** Open an Issue → receive a PR with `patch.diff`, validation logs, and verdict. Review asynchronously.

### P4 — Advanced Guardrails
- **Qualitative Risk Gates**
  - Move beyond line counts to classify risks by file type (e.g., `schemas/` = HIGH, `tests/` = LOW).
  - Connect to the async worker flow: HIGH risk blocks auto-apply; LOW risk proceeds.

### P5 — Formalization
- **Experiment Framework & Metrics**
  - Track success rates, diff accuracy, and failure modes over multiple experiments.
- **Defense in Depth (deferred to post-evidence)**
  - Auto-seeding of characterization tests for uncovered code.
  - Shadow patching for untestable legacy functions.
  - Only if dogfooding reveals real repos that break the current pipeline.

---

## Summary of Logic

The sequence is designed to move from **Stability** → **Contracts** → **Evidence** → **Scale**.

We do not attempt self-improvement until the tool is stable enough to fail safely (Rollback) and can interpret its instructions precisely (Structured Parsing). We do not invest in Defense in Depth until dogfooding generates empirical evidence of real failure modes. We target developer friction first because it is the immediate bottleneck to adoption, and solving it with async workers creates the infrastructure needed for enterprise deployment.

**Critical path update:** ADR-01 (schema versioning policy) is now a P2 entry condition, not a post-hoc response to empirical failures. Dogfooding by construction creates artifacts with cross-version lifespans; the architecture must define the compatibility mechanism before those artifacts exist, not after.
