# Technical Debt Discoveries

> Log of technical debt discovered during issue implementation that was outside the issue scope.
> Entries are added by the Diff Reviewer step during implementation (step 11).
> Periodically reviewed and promoted to `reference.md` (Known Technical Debt) during maintenance.

## Entry Format

```markdown
### [YYYY-MM-DD] Issue #N — Title

- **File:** `path/to/file.py:123`
- **Debt:** Concise description of the problem
- **Discovered by:** Diff Reviewer / Implementation
- **Why deferred:** Not part of issue scope (non-goal)
```

---

## Log

*(No entries yet)*

### [2026-06-11] Issue #77 — Pre-existing ruff formatting violations

- **File:** `scripts/bootstrap_check.py`, `tests/test_run_metadata.py`
- **Debt:** Two files have formatting violations per `ruff format --check .` that were not introduced by this issue. Blocking `ruff format --check .` from passing.
- **Discovered by:** Implementation
- **Why deferred:** Outside issue scope; auto-fix via `ruff format` is needed for pre-commit compliance.

### [2026-06-11] Issue #71 — Exception hierarchy (T-07 Part A)

- **File:** `src/orchestrator/agents/scout.py:145`
- **Debt:** Bare `raise` in `call_gemini()` except block propagates the original exception type instead of `ProviderError`, making downstream `except PatchForgeError` handlers unable to catch it. The `raise ProviderError("gemini", ...)` on line 147 is dead code — it never executes because the bare `raise` always exits before reaching it.
- **Discovered by:** AI review bot during T-07 Part A implementation
- **Why deferred:** Fix would be a behavioral change; explicitly out of scope for T-07 Part A (structural only). Deferred to T-07 Part C (tightening except clauses).
