# PatchForge — Product Thesis V2

> **Date:** 2026-06-10
> **Status:** Final — validated by 26 adversarial attacks against 9 architectural invariants
> **Supersedes:** V1 thesis (pre-adversarial, pre-invariant specification)

PatchForge is a **trust interface between a human problem and an AI-generated solution**. It is not an "autocomplete for code." It is not an "autonomous agent." It is a **verifiable pipeline** that accepts a structured specification and produces a diff validated against real tests.

---

## 1. The Contract

Three commands. Each has a single responsibility. No monolithic `run`.

| Command | What it does | Guarantee |
|---|---|---|
| `patchforge doctor` | Diagnoses the environment before any operation | Zero AI, zero side effects. Fails if git is not configured, ruff/pytest are absent, or the workspace is unsafe. |
| `patchforge plan [target=.] [--budget=low\|medium\|high]` | Analyzes the repository, generates a structured plan | Risk gate integrated: if the plan exceeds the risk budget, it is blocked before LLM cost is incurred. The plan is a DTO verifiable against system invariants. |
| `patchforge apply [--dry-run]` | Executes the approved plan, generates diff, validates against tests | Automatic rollback if validation fails. The diff exists *before* any source file is modified. |

**There is no `run`.** There is no "autonomous mode." The human decides when to move from plan to apply. This separation is architectural, not cosmetic — it is enforced by Invariant #1 (pipeline orchestrates only, no business logic).

---

## 2. The Artifact

Every invocation of `plan` or `apply` produces a directory `runs/<run_id>/` with this contract:

```
runs/<run_id>/
├── run.json              # Metadata: run_id, timestamp, schema_version (ADR-01),
│                         # status (planned|applied|aborted|awaiting_review),
│                         # mode (direct|clone), risk_verdict, cost_llm,
│                         # commit_anchor, exit_code, error if aborted
├── plan.json             # Plan: task list, target files, expected diff,
│                         # risk per task (based on file type)
├── patch.diff            # THE RESULT. Unified diff against the repository.
│                         # Exists only if the plan was approved and executed.
├── validation.json       # ruff + pytest results: exit_code, errors,
│                         # tests passed/failed/skipped
└── failure.json          # Only on abort: failure type (PipelineAbort,
                          # SchemaValidationError, rollback_error, timeout),
                          # stage where failure occurred, chained exception
```

**Architectural properties this guarantees:**

- **Round-trip stability (Invariant #2):** `run.json` can be reloaded by a future PatchForge version and produce the same DTO — exactly what stage N produced. Validators are deterministic and depend only on serialized fields.
- **Source of truth (Invariant #3):** Every stage transition passes through `workspace.load()`. If the process dies between stages, recovery is possible from the persisted artifact. In-memory copy is prohibited by architectural definition.
- **DTO purity (Invariant #9):** No downstream stage needs the LLM log or the JSON extraction position. The DTO is semantically self-contained — meaning equals representation. Provenance (how the DTO was selected) and meaning (what the DTO specifies) are orthogonal.
- **Crash recovery (T-02):** If `apply` fails mid-patch, the repository returns to its previous state. `failure.json` documents exactly what failed and at which stage.
- **Temporal separation (Invariant #3, clarified):** A `ValidationError` at reload time cannot be an agent contract violation. Under same-schema conditions, Invariant #2 guarantees the artifact was valid when produced — reload failure implies persistence corruption. Under cross-schema conditions, the agent correctly implemented the schema it was given; the incompatibility is evolution.

---

## 3. The Experience

### Scenario A — Individual Developer

```bash
cd my-project
patchforge doctor                  # ✅ Environment ready
patchforge plan target=.           # Plan generated, 3 tasks, $0.14
# Reviews plan.json. Decides to apply.
patchforge apply                   # Diff generated, validated, ready
# Reviews patch.diff. Runs git commit.
```

The human never touches generated code before reviewing it. The diff is the deliverable. The system never modifies files without prior validation.

### Scenario B — CI/CD Enterprise

A GitHub Issue triggers a worker that runs PatchForge against a clone of the repository. The output is a PR with `patch.diff`, `validation.json`, and `verdict.md`. A human reviews the PR and merges.

Same contract as Scenario A. Only the invoker changes (worker vs terminal). The artifacts, guarantees, and review flow are identical.

### Scenario C — Dogfooding (PatchForge improves PatchForge)

An Issue in the PatchForge repository with explicit ACs triggers an experiment. The system clones itself, executes the plan against the clone, validates against its own tests, and produces a PR. A human reviews.

The innovation is not autonomy — it is that **the same pipeline used by users applies to the product itself.** There is no special code for "self-improvement." There is an Issue, a plan, a diff, validation, and human review. Nothing more.

---

## 4. Explicit Non-Goals

| Non-goal | Why |
|---|---|
| **Autonomous self-improvement without human review** | The pipeline requires human diff review before apply. This is not a technical limitation — it is the product thesis. |
| **Execution on repositories without tests** | PatchForge validates against `pytest`. Without tests, there is no validation. Defense in Depth (auto-seeding characterization tests) is P4 on the roadmap but does not exist today. |
| **Monolithic `run` command** | No command chains plan+apply automatically. The separation is intentional: the human must approve the plan before any diff is generated. |
| **Natural language Issue interpretation** | Issue Contracts with structured ACs (Issue B) are P1 on the roadmap. Today PatchForge operates on files, not free text. |
| **Cross-version artifact compatibility without ADR** | ADR-01 must be resolved before P2. Until then, artifacts are only valid within the same PatchForge version that produced them. |
| **Modification without validation** | Every code change goes through: plan → risk gate → apply → validate (ruff + pytest) → human review. No shortcuts. |

---

## 5. The Thesis in One Sentence

PatchForge does not replace the developer. It replaces the "modify and pray" phase with a **verifiable artifact that the developer reviews before any modification occurs.**

The product is not generated code. The product is **demonstrable trust**. Every `run/` is a reproducible experiment. Every `failure.json` is a lesson, not a silent error. Every `patch.diff` reviewed by a human is a decision, not a delegation.
