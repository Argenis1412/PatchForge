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

### ✅ [2026-06-15] Phase 3 — `run_ruff()` mutates caller `cmd_override` (RESOLVED)

- **File:** `src/orchestrator/agents/validator/runners.py:130`
- **Debt:** `run_ruff()`, `run_pytest()`, and `run_tsc()` assign `cmd = cmd_override`
  without copying, then `run_ruff()` mutates via `cmd.extend()`. The caller's
  original list object is polluted for any subsequent usage.
- **Discovered by:** CodeRabbit during Phase 3 extraction review
- **Resolution:** All 6 `cmd_override` assignments now use `list(cmd_override)`
  to create a defensive copy. Fix branch `fix/cmd-override-mutation`.

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

### ✅ [2026-06-14] Experiment 002 — Groq API 403 (key expired/rate-limited) (RESOLVED)

- **File:** `src/orchestrator/agents/executor.py`
- **Debt:** Groq API key returns 403 Forbidden. All medium-risk tasks route to Groq;
  when Groq is unavailable, the pipeline stalls. No fallback chain exists
  (Groq → Gemini → Claude).
- **Discovered by:** Experiment 002 dogfooding
- **Resolution:** Issue #100 implemented a unified provider fallback chain that
  handles all recoverable provider errors (CB open, 403, rate limits, etc.)
  across all risk levels.

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


### [2026-06-15] Phase 4 — Provider clients lack consistent timeout

- **File:** `src/orchestrator/clients/gemini_client.py:11`, `anthropic_client.py:11`, `groq_client.py:16`
- **Debt:** All three provider clients have inconsistent or missing timeouts:
  - Gemini: `genai.Client()` has no timeout — requests can hang indefinitely.
  - Anthropic: uses SDK default (10 min) instead of `TIMEOUT_SECONDS` (60s).
  - Groq: hardcodes 30s instead of `TIMEOUT_SECONDS` (60s).
  The `TIMEOUT_SECONDS` constant exists in `providers.py` but no client consumes it.
- **Discovered by:** CodeRabbit AI review during Phase 4
- **Why deferred:** Clients live in `clients/*.py`, outside the executor extraction scope. Fixing requires deciding between per-request timeout (less invasive) or refactoring `get_*_client()` to accept a timeout parameter.

### ✅ [2026-06-15] Phase 4 — `__init__.py` import binding prevents submodule monkeypatch (RESOLVED)

- **File:** `src/orchestrator/agents/executor/__init__.py` (general pattern)
- **Debt:** When `__init__.py` does `from .applier import _apply_task`, the binding is captured at import time. Monkeypatching `applier._apply_task` does not affect `run()`. The fix was to import the module (`from . import applier as _applier`) and access via `_applier._apply_task()`. This pattern is not documented as a convention, making it easy to reintroduce the bug in future extractions (Phase 5-7).
- **Discovered by:** Phase 4 execution (8 tests failed due to ineffective monkeypatch)
- **Resolution:** Phase 4.5 — `docs/import-convention.md` documents the lazy import pattern inside function bodies, with GOOD/BAD examples and a monkeypatch rationale.

### [2026-06-15] Phase 4 — Dead `mock_groq` fixture in conftest.py

- **File:** `tests/conftest.py:30-37`
- **Debt:** The `mock_groq` fixture patches `orchestrator.agents.executor._call_groq` but no test in the suite uses it. Dead code. Furthermore, even if a test did use it, it would not work — `_PROVIDER_CHAIN` stores references to `_call_groq` at import time, so the monkeypatch would have no effect.
- **Discovered by:** Phase 4 dependency audit
- **Why deferred:** Outside refactor scope. Clean up in a housekeeping issue.

### [2026-06-15] Phase 4 — `PROJECT_ROOT` depends on `__file__` — brittle on relocation

- **File:** `src/orchestrator/agents/executor/__init__.py:25-27`
- **Debt:** `PROJECT_ROOT` resolves via `Path(__file__).resolve().parent.parent.parent.parent`. This required an extra `.parent` when moving from `executor.py` to `executor/__init__.py`. Every time a module moves within the `agents/` tree, any `__file__`-based path constant silently breaks. Should use `PROJECT_ROOT` from a shared module or always via environment variable.
- **Discovered by:** Phase 4 execution
- **Why deferred:** Pre-existing behavior in scout, architect, validator. Do not change without a unified strategy for all 4 agents.

### [2026-06-14] Issue #100 — Agent fallback inconsistency

- **File:** `src/orchestrator/agents/validator.py`
- **Debt:** The executor now uses a resilient, unified fallback chain via _call_chain().
  However, the validator agent still uses a primitive, manual fallback (returning
  raw stderr) when Gemini is unavailable. This creates an architectural
  inconsistency and leaves the validation stage less resilient than the execution stage.
- **Discovered by:** Implementation of Issue #100
- **Why deferred:** Out of scope for Issue #100, which specifically targets the
  executor pipeline. Correcting this requires extracting the chain logic into a
  shared utility.
