# PatchForge: Product Thesis

## The Problem

Most AI tools for coding optimize for speed.

PatchForge optimizes for confidence.

Today, the dominant workflow is:

```text
Prompt
  ↓
LLM
  ↓
Direct changes to the repo
```

This works for small tasks.

But it becomes problematic when:

- The repository grows.
- Multiple contributors exist.
- There are audit requirements.
- Changes impact multiple layers of the system.
- The cost of an error increases.

The AI modifies the code before proving it understood the problem.

PatchForge inverts that order.

---

# The Core Idea

PatchForge does not generate code.

PatchForge generates auditable changes.

The unit of value is not:

- The agent
- The prompt
- The model
- The context

The unit of value is:

```text
Patch
```

Everything revolves around the patch.

```text
Scan
 ↓
Plan
 ↓
Patch
 ↓
Validate
 ↓
Apply
```

If there is no clear and reviewable patch:

```text
Value does not exist.
```

---

# Core Principles

## 1. Never modify first

Most AI tools follow this flow:

```text
Analyze
 ↓
Modify
 ↓
Hope it works
```

PatchForge works like this:

```text
Analyze
 ↓
Plan
 ↓
Generate patch
 ↓
Validate
 ↓
Approve
 ↓
Modify
```

The repository is treated as a critical system.

---

## 2. The user remains in control

PatchForge never assumes final authority.

There is always:

```bash
git diff
```

There is always:

```bash
patchforge preview
```

There is always:

```bash
patchforge apply
```

The user decides when the code changes.

---

## 3. Git is the single source of truth

No hidden databases.

No magic states.

No uninspectable memory.

Everything must be reconstructible from:

```text
Git
Artifacts
Events
```

PatchForge is Git-native.

GitHub, GitLab, or any other platform are optional.

Git is mandatory.

---

## 4. Reproducibility

Every run must answer:

```text
What changed?
Why did it change?
What commit was analyzed?
What model participated?
What validations passed?
What events occurred?
```

If these cannot be answered:

```text
The execution failed.
```

---

## 5. Expensive models only where they add value

Common industry mistake:

```text
LLM for reading
LLM for searching
LLM for navigating
LLM for deciding
LLM for modifying
```

PatchForge minimizes inference.

The AI participates only where it brings judgment:

```text
Architecture
Design
Refactoring
Complex resolution
```

Everything else must be:

```text
Deterministic
```

---

# New Principle: Patches Have Context

A diff shows:

```text
What changed
```

But it does not explain:

```text
Why it changed
```

PatchForge must generate auditable artifacts around the patch.

Example:

```text
run_001/
├─ patch.diff
├─ run.json
├─ plan.json
├─ validation.json
├─ findings.json
└─ events.jsonl
```

The patch remains Git-compatible.

The explanation remains available to humans.

No proprietary format is required to inspect an execution.

---

# New Principle: Patches Expire

A patch represents a proposal on a specific state of the repository.

That state can change.

Therefore:

```text
Patches have validity periods.
```

Every patch must record:

```json
{
  "base_commit": "abc123",
  "generated_at": "...",
  "files_analyzed": [...]
}
```

Before applying a patch, PatchForge verifies if the original context is still valid.

---

## Possible States

### VALID

```text
The repository remains compatible.
```

The patch can be applied.

---

### STALE

```text
The context has changed.
```

The patch must be regenerated.

---

### REBASEABLE

```text
The context has changed slightly.
```

PatchForge can attempt to adapt the change.

---

### CONFLICT

```text
Incompatible changes exist.
```

Human intervention is required.

---

# What PatchForge is NOT

PatchForge is not:

- An IDE.
- An editor.
- A chatbot.
- A copilot.
- An agent framework.
- A model wrapper.

The existence of agents is an internal detail.

Users do not buy agents.

They buy confidence.

---

# Differentiation against competitors

## Aider

### Philosophy

```text
Iteration speed
```

Model:

```text
Prompt
 ↓
Direct changes
```

### PatchForge Advantage

```text
Patch first
Application later
```

More control.

More traceability.

More confidence.

---

## Plandex

### Philosophy

```text
Planning via massive context
```

Model:

```text
Heavy context
 ↓
Plan
 ↓
Implementation
```

### PatchForge Advantage

```text
Less context
More structure
```

PatchForge seeks to understand only what is necessary.

Not the entire repository.

---

## Sweep

### Philosophy

```text
Issue
 ↓
Code
 ↓
PR
```

### PatchForge Advantage

```text
Git-native
```

Does not depend on GitHub.

Does not depend on any platform.

It depends on Git.

---

## OpenHands / OpenDevin

### Philosophy

```text
Goal
 ↓
Autonomous Agent
 ↓
Actions
 ↓
More Actions
```

### PatchForge Advantage

```text
Determinism
```

over

```text
Autonomy
```

The priority is not to do more.

The priority is to prove what was done.

---

# The Real Competitive Advantage (Moat)

The advantage will not be:

- More agents.
- More models.
- More context.
- More tools.

All of that is replicable.

The real advantage will be:

```text
Operational confidence.
```

When a developer sees a patch generated by PatchForge, they should think:

```text
This is probably correct.
```

Not:

```text
It probably broke something.
```

---

# Correct Strategic Roadmap

The natural evolution is not:

```text
More agents
```

The correct evolution is:

```text
More guarantees
```

Examples:

- Risk Scoring
- Impact Analysis
- Patch Confidence
- Dependency Awareness
- Test Coverage Awareness
- Rollback Intelligence
- Change Forecasting
- Stale Patch Detection
- Incremental Repository Understanding

---

# The Competitive Moat

Agents can be copied.

Prompts can be copied.

Models change every few months.

The true competitive moat is building a system capable of answering:

```text
Can I trust this change?
```

Faster and with more evidence than any competitor.

---

# Vision

Most AI tools try to replace the developer.

PatchForge tries to solve a different problem:

Creating a layer of trust between the AI and the repository.

```text
Human
    ↓
PatchForge
    ↓
Repository
```

The AI proposes.

PatchForge demonstrates.

The human decides.

---

# Mission

To turn AI-generated changes into auditable, reviewable, reproducible, and safe modifications.

We do not pursue maximum autonomy.

We pursue maximum confidence.

Because in software engineering, speed matters.

But confidence determines who can use that speed in production.
