# PatchForge Scout — Long-Term Vision

> **Date:** 2026-07-11
> **Status:** Frozen. Not on the Core roadmap. Do not open Core issues from this document.
> **Purpose:** Preserve the strategic vision for a second product line so it does not creep into Core planning.

---

## Decision Record

**2026-07-11** — After ideation and adversarial review, decided that Scout is a **separate product line**, not a PatchForge Core feature.

- **PatchForge Core** answers: *how do I execute changes safely?*
- **PatchForge Scout** answers: *what changes are worth executing?*

Both share the pipeline via the `IssueContract`. Neither depends on the other's implementation.

**Sequence commitment:**
1. Complete PatchForge Core P4/P5.
2. Reach a stable Core 2.x release.
3. Publish `IssueContract` as a stable public schema (P4 ships this — see `roadmap.md`).
4. Only then start Scout in a separate repository, consuming `IssueContract` as its output contract.

**Hard rule:** Scout must not delay Core. Any Scout idea that surfaces during Core work goes to the backlog below — it does not become a Core issue.

---

## The Vision

Scout occupies the gap between **detection** (Semgrep, Ruff, CodeQL, coverage, git churn) and **execution** (Claude Code, PatchForge, Codex, Cursor). Detection tools produce disconnected findings. Execution tools require well-formed instructions. Scout is the intelligence layer that transforms evidence into an actionable, ranked backlog of issues an AI agent can consume with minimal human intervention.

Scout **does not detect problems**. It **understands problems**. Detection belongs to specialized tools. Intelligence belongs to Scout.

### What Scout Answers

- What is the actual root cause?
- What system behavior is affected?
- How confident is this conclusion?
- What is the scope of the change?
- What constraints must the fix respect?
- What prompt should an AI agent receive to resolve exactly this problem?

### Architecture (conceptual)

```text
Repository
    │
    ▼
Detection Layer         (Semgrep, Ruff, CodeQL, churn, coverage, type checker)
    │
    ▼
Correlation Engine      (normalize, dedupe by file/module/symbol, rank)
    │
    ▼
Intelligence Engine     (root cause, blast radius, constraints, prompt)
    │
    ▼
Issue Registry          (structured issues consumable by any agent — YAML/SARIF/JSON)
```

---

## Agreed Design Cuts (do not violate when Scout starts)

These cuts came out of the adversarial review of the initial Scout proposal. They are load-bearing — expanding beyond them turns Scout into a research project.

- **Correlation is file/module/symbol only.** Causal correlation between findings across the graph (Semgrep rule X → coverage gap Y → churn Z as a single causal chain) is a research problem. Correlate on shared *location*, not shared *causality*.
- **Evidence Graph, if ever built, consumes external graphs.** LSP, tree-sitter, Pyright, Jedi, CodeQL — these produce graphs Scout can annotate. Scout never builds interprocedural analysis from scratch. That's territory where projects die.
- **No standalone "attack prompt" component.** Scout emits `IssueContract` — the exact same schema the Planner/Architect already consume. If we build two prompt generators (one in Scout, one in Architect) they diverge within a quarter.
- **Objective confidence, not model self-report.** Confidence is computed from convergent independent signals (finding count, coverage, churn percentile, cross-tool agreement). The LLM never states its own confidence. Confidence must be auditable.
- **Structured, slotted prompts.** Scout produces prompts with named slots (Context / Problem / Evidence / Scope / Constraints / Acceptance / Files / Known Risks). Never free text.
- **Open formats.** Input via SARIF where possible; output via `IssueContract`. Scout is an interoperability layer, not another walled scanner.

---

## Repository Intelligence Database (Scout's Future Moat)

Scout's real long-term value is not analyzing the *current state* of a repo — every scanner does that. It's analyzing the *historical behavior* of the repo. Every Core run leaves data. Over time an accumulated ledger of per-file behavior becomes something no static scanner can compete with:

```text
providers.py
  changed        34 times
  failures       5
  rollbacks      2
  review required 8
  avg complexity 18
  avg validation time 34s

planner.py
  touched        89 times
  never rolled back
  coverage       97%
  low-risk classification
```

The Ledger (P5-1 in Core) is the data source. Scout consumes it via a stable read interface. This is why the Ledger belongs in Core (it makes every run better today) and why the analytics layer belongs in Scout (it makes future runs smarter).

Building this feeds Scout's ranking function with signals no first-time scan can produce:
- "This file has a history of failing validation → weight risk higher."
- "This module has never been rolled back → weight confidence higher."
- "This complexity + this churn + this coverage → prioritize this over five other findings."

---

## Backlog — Scout ideas discovered during Core work

Seed entries. Add new ones here whenever a Scout-shaped idea surfaces during Core P4/P5 work.

- **Semantic AC mapping.** Cut from AC Compiler (Core P5-4). Mapping prose ACs like "add Claude as fallback" to specific constructs (`_call_chain([...])`) requires semantic understanding of the repo — not string search. Scout territory.
- **Cross-tool correlation heuristics.** When Semgrep + Ruff + coverage all flag the same file within N days, weight higher.
- **File hotness signals from git.** Author diversity, comment density in changes, review round-trip time.

---

## Non-Goals for Scout (both now and when it starts)

- **Not a scanner.** Detection stays in Semgrep/Ruff/CodeQL.
- **Not an agent.** Execution stays in PatchForge Core (or any other agent consuming `IssueContract`).
- **Not part of the Core repo.** When implementation begins, Scout lives in its own repository.
- **Not a P4/P5 deliverable.** No Core issue may be filed under "Scout" until step 3 of the sequence commitment above is met.
