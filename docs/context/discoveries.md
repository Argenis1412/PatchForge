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

### ✅ [2026-06-14] Issue #79 — `write_verdict()` I/O in schemas/ (RESOLVED)

- **File:** `src/orchestrator/schemas/experiment.py`
- **Debt:** `write_verdict()` co-locates file I/O with schema definition.
  The codebase pattern puts I/O in `workspace.py`. Consistent with this
  issue's scope (minimal, no pipeline touch) but inconsistent with the
  established pattern.
- **Discovered by:** Implementation
- **Resolution:** Moved to `WorkspaceManager.write_verdict()` in `workspace.py`
  as part of Experiment 002. `schemas/experiment.py` now contains only the
  pure `Verdict(BaseModel)` schema.

### ✅ [2026-06-14] Experiment 002 — Executor skips dependent tasks when dependency reports "already applied" (RESOLVED)

- **File:** `src/orchestrator/agents/executor.py`
- **Debt:** When a task dependency (e.g. T1 — audit) produces "no changes — already applied",
  the executor skips downstream tasks (e.g. T2 — add to workspace.py) even though
  T2 is not a no-op. The task dependency DAG is flattened into a linear sequence
  and adjacent skip logic poisons the chain.
- **Discovered by:** Experiment 002 dogfooding
- **Resolution:** Issue #98 replaced the flat sequential loop with a DAG scheduler
  (Kahn's topological order) that respects `Task.dependencies`, detects cycles,
  and propagates `SKIPPED` status correctly. The placeholder "no changes — already applied"
  string was replaced by `TaskStatus.NOOP` with `diff=None`.

### [2026-06-14] Experiment 002 — Groq API 403 (key expired/rate-limited)

- **File:** `.env`
- **Debt:** Groq API key returns 403 Forbidden. All medium-risk tasks route to Groq;
  when Groq is unavailable, the pipeline stalls. No fallback chain exists
  (Groq → Gemini → Claude).
- **Discovered by:** Experiment 002 dogfooding
- **Why deferred:** API key management is outside the codebase scope; fix requires
  provider-agnostic task routing (fallback chain) or a valid key.

### [2026-06-14] Experiment 002 — Risk budget defaults too restrictive for multi-file refactors

- **File:** `src/orchestrator/commands/scan.py:138-140`
- **Debt:** `risk_budget="low"` and `max_files=2` block refactors of 3+ files.
  A pure refactor (code movement only, no logic change) should not require
  manual `run.json` editing.
- **Discovered by:** Experiment 002 dogfooding
- **Why deferred:** Out of scope of Experiment 002; requires a `--risk-budget` flag
  or auto-escalation for no-logic-change refactors.

### [2026-06-11] Issue #77 — Pre-existing ruff formatting violations

- **File:** `scripts/bootstrap_check.py`, `tests/test_run_metadata.py`
- **Debt:** Two files have formatting violations per `ruff format --check .` that were not introduced by this issue. Blocking `ruff format --check .` from passing.
- **Discovered by:** Implementation
- **Why deferred:** Outside issue scope; auto-fix via `ruff format` is needed for pre-commit compliance.

### [2026-06-11] Issue #77 — RunMetadata.schema_version default duplicado

- **File:** `src/orchestrator/schemas/artifacts.py:47`
- **Debt:** `schema_version: int = 1` hardcodea el valor en lugar de usar `schema_version: int = CURRENT_SCHEMA_VERSION`. Si alguien incrementa la constante pero omite el default del campo, `RunMetadata` produciría artifacts con versión incorrecta.
- **Discovered by:** AI review bot (CodeRabbit)
- **Why deferred:** Modificar field defaults de `RunMetadata` está fuera del scope de ADR-01/3. Corregible en cualquier issue futuro que toque `artifacts.py`.

### [2026-06-13] Issue #87 — Circuit Breaker (T-07 Part B)

- **File:** `src/orchestrator/circuit_breaker.py`
- **Debt:** `CircuitBreaker._consecutive_failures` y `_half_open_in_flight` no tienen protección thread-safe. Consistente con el patrón existente de `clients/*.py` (sin locks, GIL-dependent), pero si P3 introduce threading o async workers, será un race condition.
- **Discovered by:** Adversarial audit durante diseño de la issue
- **Why deferred:** No-threading es invariante del proyecto en V1. Revisar con P3 (async workers).

- **File:** `src/orchestrator/exceptions.py:101`
- **Debt:** `CircuitBreakerOpenError.state` tiene type hint `object` en vez de `CircuitBreakerState` para evitar import circular entre `circuit_breaker.py` y `exceptions.py`. No afecta runtime.
- **Discovered by:** Implementation
- **Why deferred:** Romper el import circular requiere mover `CircuitBreakerState` a un tercer módulo o hacer `exceptions.py` importar de `circuit_breaker`. Fuera de scope de T-07B.

### [2026-06-11] Issue #71 — Exception hierarchy (T-07 Part A)

- **File:** `src/orchestrator/agents/scout.py:145`
- **Debt:** Bare `raise` in `call_gemini()` except block propagates the original exception type instead of `ProviderError`, making downstream `except PatchForgeError` handlers unable to catch it. The `raise ProviderError("gemini", ...)` on line 147 is dead code — it never executes because the bare `raise` always exits before reaching it.
- **Discovered by:** AI review bot during T-07 Part A implementation
- **Why deferred:** Fix would be a behavioral change; explicitly out of scope for T-07 Part A (structural only). Deferred to T-07 Part C (#90) which explicitly preserved the bare-raise behavior as part of scout's error-surface contract. This design decision creates the debt documented above. Remains unresolved pending future issue.
