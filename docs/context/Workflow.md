# Development Workflow — PatchForge

## Quick Reference (daily)

```bash
# QA before commit
ruff check .
ruff format --check .
pytest

# Periodic audit (every 5 PRs)
patchforge scan "."

# Commits
git commit -m "<type>(<scope>): <message>"

# Branch
git checkout -b <type>/issue-<N>-<slug>
```

> Live project status lives in [CONTEXT.md](CONTEXT.md). This document covers process only.

---

Short version of the flow:

```
issue → clarify → criteria → challenge → plan → adversarial review → approval
→ implement → diff review → tests → QA → commit → push
```

Rules:
- **The refactor is never the unit of work.** The unit is a self-contained issue with limited scope and verifiable criteria.
- Stop and ask if anything is ambiguous
- Implement only what the issue requires
- No unrelated refactors
- Keep diffs minimal
- Branch: `<type>/issue-<N>-<slug>`
- Code, comments, commits and PRs in **English**
- Conventional commits
- QA required before commit: `ruff check` · `ruff format --check` · `pytest`
- Behavioral changes require tests
- Documentation-only changes do not require tests unless explicitly requested
- Never unnecessarily summarize or rewrite existing code

---

## Steps

1. Read and understand the issue
2. Run **AI: Issue Clarifier**
3. Update the issue if gaps are found
4. Define acceptance criteria
5. Run **AI: AC Challenger**
6. Create implementation plan
7. Run **AI: Adversarial Reviewer**
8. Wait for approval
9. Create branch: `git checkout -b <type>/issue-<N>-<slug>`

> **Phase docs:** Before opening a GitHub issue for a P4/P5 item, start from the per-item doc under `docs/planning/p4/` (or `p5/` when available).

10. Implement
11. Run **AI: Diff Reviewer**
12. Add/update tests if needed
13. Run QA (`ruff check .` · `ruff format --check .` · `pytest`)
14. Create atomic commit: `git commit -m "<type>(<scope>): <message>"`
15. Push the branch: `git push origin <branch>`
16. Generate PR description

Do not skip steps.

---

## Acceptance Criteria

Before creating a plan:

- Extract acceptance criteria from the issue
- If criteria are missing, propose them
- Identify non-goals (what will not be done)
- Identify ambiguities
- Stop and ask if something is not clear

Suggested format:

```
## Acceptance Criteria

- AC1
- AC2
- AC3

## Exit Conditions

This issue is considered complete when:
- AC1, AC2, AC3 are met
- No remaining derived tasks are left

## Out of Scope by Construction

Even though they may seem related, these items MUST NOT be touched:
- file_x.py (reason)
- parser.py
- public API
- docs

## Non-Goals

- Item 1
- Item 2

## Open Questions

- Question 1
```

---

## Planning Rules

When creating an attack plan, return:

```
## Diagnosis

Current state of the code.

## Files to Modify

- file_a.py
- file_b.py

## Implementation Plan

Step-by-step changes.

## Tests

New tests required.
Existing tests affected.

## Risks

Possible regressions or edge cases.

## Modification Budget

Numerical limits the implementation must not exceed:

- `max_files_modified: N`
- `max_public_api_changes: N`
- `max_new_dependencies: N`
- `max_new_modules: N`
- `max_new_classes: N`

If the implementation requires exceeding any limit, stop and explain why. Do not implement.

## Out of Scope

Explicit list of what will NOT be changed.
```

Do not implement during planning.

---

## Implementation Rules

- Implement only what the issue requires
- Keep diffs minimal
- No unrelated refactors
- No speculative improvements
- No code rewrites unless necessary
- Preserve existing style and architecture
- Reuse existing patterns when possible

If you discover technical debt outside the issue scope (not caused by the issue):

- Document it in `docs/context/discoveries.md` following the defined format
- Do not fix or modify it
- The discovery commit goes separately: `chore(docs): log td discovery in issue #N` (only if there are changes to discoveries.md)

---

## Branch Naming

Format:

```
<type>/issue-<number>-<slug>
```

Allowed types: `feat` · `fix` · `docs` · `refactor` · `chore`

Examples:

```
feat/issue-45-add-user-cache
fix/issue-31-handle-empty-response
docs/issue-33-doctor-docstrings
```

---

## Testing Rules

| Change type            | Tests required?                        |
|------------------------|----------------------------------------|
| Behavioral change      | Yes                                    |
| Bug fix                | Yes — regression test required         |
| New feature            | Yes                                    |
| Documentation only     | No, unless explicitly requested         |
| Pure refactor          | No — existing tests must continue to pass |

---

## QA Rules

Before each commit run:

```bash
ruff check .                     # must return 0 errors
ruff format --check .            # must return clean
pytest -v
```

All three commands must pass before any commit. Do not create commits if QA fails.

Report format:

```
## QA Results

### Ruff
PASS / FAIL

### Format
PASS / FAIL

### Tests
X passed
X failed
X skipped
```

Do not create commits if QA fails.

---

## Commit Rules

Language: English only

Format:

```
<type>(<scope>): <message>
```

Examples:

```
feat(auth): add token refresh support
fix(api): handle empty response body
docs(doctor): add missing docstrings
refactor(cache): simplify cache lookup
```

One logical change per commit.

---

## Pull Request Rules

Generate the PR description in Markdown.

Suggested format:

```
# Summary

Brief explanation.

# Changes

- Change 1
- Change 2

# Files Modified

- file_a.py
- file_b.py

# Before

Previous behavior.

# After

New behavior.

# Testing

- ruff check
- ruff format --check
- pytest

Results:
- XX passed / XX failed / XX skipped

# Acceptance Criteria

- [ ] AC1
- [ ] AC2
- [ ] AC3
```

---

## Communication Rules

- Be direct
- Be concise
- Explain the reasoning
- Point out risks
- Do not hide trade-offs
- Ask before proceeding if there is ambiguity
- Never assume requirements not present in the issue

---

## AI Roles

Four specific roles. Each has a defined purpose and prompt.
Use the correct role at the correct step — do not mix them.

Each AI has a distinct responsibility. No single AI has authority to design everything.
This distributes risk: if one AI makes a mistake, subsequent ones can detect it.

---

### Role 1 — Issue Clarifier

**When:** After reading the issue, before defining acceptance criteria.

**Purpose:** Find gaps in the issue before any implementation decisions.

**Prompt:**

```
Read this issue. Find:
1. Terms used without explicit definition
2. Edge cases not covered by the criteria
3. Interactions with existing code that are not mapped
4. Ambiguities that would cause two developers to implement differently

Do not propose solutions. List only the gaps.
```

---

### Role 2 — AC Challenger

**When:** After defining criteria, before creating the plan.

**Purpose:** Stress-test the criteria before committing to a plan.

**Prompt:**

```
These are the acceptance criteria for [issue].

1. Which criteria are hard or impossible to test with a unit test?
2. Which edge cases are not covered?
3. Which criteria are redundant?
4. Which important failures are missing?

Do not propose solutions. List only problems.
```

---

### Role 3 — Adversarial Reviewer

**When:** After creating the plan, before approval.

**Purpose:** Challenge the plan before writing a single line of code.

**Prompt:**

```
This is my attack plan. Your job is to challenge it:

1. What assumptions am I making that could be false?
2. What already exists in the codebase that makes part of this unnecessary?
3. What interactions with other modules did I miss?
4. In what scenario would this plan produce a silent bug?

List problems first. Solutions only if asked.
```

---

### Role 4 — Diff Reviewer

**When:** After implementing, before writing tests.

**Purpose:** Detect logical errors and coverage gaps before QA.

**Prompt:**

```
This is my implementation diff.

1. What code paths are not covered that tests should exercise?
2. Does anything in the diff contradict the approved plan?
3. Are there any obvious logical errors or unhandled cases?

Do not evaluate style or formatting — ruff handles that.
```

---

## Continuation Context Document

Maintain a context document for each AI session.
Update it after each merged PR.

Required sections:

```markdown
## Working Style
[Summary of the 10-step flow and rules]

## Current State
[Status of each completed issue]

## Known Technical Debt
- Item description and why it was deferred

## Failed Approaches
- [date] Tried X to solve Y. Did not work because Z.

## Architecture Invariants
Things that must not change without an ADR:
- pipeline.py only orchestrates, no business logic execution
- Agents receive and produce typed Pydantic schemas
- Every stage output is persisted before the next
- main.py is CLI surface only — no business logic

## Open Design Questions
- Question and current thinking

## Next Task
[Issue number, title, and current step]
```

---

## Periodic Audit

Every 5 merged PRs, run the V1 pipeline against itself:

```bash
patchforge scan "."
```

For full AI analysis (if API keys are configured):

```bash
patchforge plan <run_id> --workspace <workspace_path>
patchforge preview <run_id> --workspace <workspace_path>
```

Review:
- Did the scanner detect the correct hotspots?
- Are the findings still accurate?
- Are QA metrics holding?

Document results in `docs/context/reference.md` (Known Technical Debt or Open Design Questions).

---

## Golden Rule

Implement the smallest correct change that satisfies all acceptance criteria.
