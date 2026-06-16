# Refactoring Phases

> Zero behavior changes. Extract only, never rewrite.

---

## Phase 0 — Baseline ✅ (done)

**Branch:** `feat/baseline-phase-0`

### Files added

- `tests/test_baseline_cli.py` — 10 characterization tests for `doctor` and `scan` (offline CLI commands)
- `scripts/baseline_metrics.py` — deterministic AST-based structural metrics generator
- `docs/baseline/metrics.json` — captured structural metrics (file sizes, function sizes, docstrings, type annotations)

### Metrics

| Metric | Value |
|--------|-------|
| `ruff check .` | 0 errors |
| `ruff format --check .` | Clean |
| `pytest` | 330 passed / 2 skipped |
| Files >500 lines | 6 |
| Files >1000 lines | 0 |
| Functions >100 lines | 11 |
| Functions >50 lines | 30 |

---

## Phase 1 — `agents/scout.py` → `agents/scout/` ✅ (done)

**Branch:** `feat/phase-1-scout`
**Commit:** `fdaf70d`

**Risk:** Low — internal module, few consumers.

| File | Content |
|------|---------|
| `agents/scout/__init__.py` | `run()`, `read_file_tree()`, `read_selected_files()`, prompts, `__main__` |
| `agents/scout/provider.py` | `call_gemini()`, `MODEL`, cost constants |

---

## Phase 2 — `agents/architect.py` → `agents/architect/` ✅ (done)

**Branch:** `feat/phase-2-architect`
**Commit:** `63540df`

**Risk:** Low — internal module.

| File | Content |
|------|---------|
| `agents/architect/__init__.py` | `run()`, `run_from_issue()`, `__main__` |
| `agents/architect/prompts.py` | `ARCHITECT_PROMPT`, `ISSUE_ARCHITECT_PROMPT` |
| `agents/architect/provider.py` | `call_claude()`, `MODEL`, cost constants |

---

## Phase 3 — `agents/validator.py` → `agents/validator/` ✅ (done)

**Branch:** `feat/phase-3-validator`
**Commit:** `eafd107`

**Risk:** Low-Medium — logger singleton, circuit breaker, multiple monkeypatch paths.

| File | Content |
|------|---------|
| `agents/validator/__init__.py` | `run()`, `_cb_validator`, re-exports, `__main__` |
| `agents/validator/logging.py` | `_logger`, `_get_logger()` singleton |
| `agents/validator/runners.py` | `_run()`, `run_ruff()`, `run_pytest()`, `run_tsc()`, helpers |
| `agents/validator/summarizer.py` | `_summarize_errors()`, `MODEL_GEMINI`, `COST_PER_SUMMARY` |

---

## Phase 4 — `agents/executor.py` → `agents/executor/` ✅ (done)

**Branch:** `feat/phase-4-executor`
**Commit:** `f8f32fc`

**Risk:** Medium — provider failover, circuit breaker, DAG scheduling, task application.
Mitigated by full dependency audit before extraction.

| File | Content |
|------|---------|
| `agents/executor/__init__.py` | `run()`, `PROJECT_ROOT`, `__main__`, re-exports `rollback_to_commit` |
| `agents/executor/logging.py` | `_logger`, `_get_logger()` singleton |
| `agents/executor/providers.py` | Provider orchestration, circuit breaker, `_call_chain` |
| `agents/executor/scheduler.py` | `_build_dag()`, `_topological_order()` |
| `agents/executor/applier.py` | `_build_prompt()`, `_apply_task()` |
| `agents/executor/rollback.py` | `rollback_to_commit()`, revert |
| `agents/executor/diffing.py` | `_make_diff()` — consolidated diff generation |

---

## Phase 5 — `main.py` → `commands/apply.py` ✅ (done)

**Branch:** `refactor/extract-apply-from-main`
**Commit:** `9fa76de`

**Risk:** Medium — public CLI, but `commands/*.py` pattern already exists.
Mitigated by Phase 4.5 (import binding convention doc + dead code removal).

| File | Content |
|------|---------|
| `commands/apply.py` | `execute()` (406 lines, verbatim from `main.py`) |
| `main.py` | ~155 lines (Typer definitions, `_load_target_config`, delegation only) |
| `test_cli.py` | 3 `patch()` paths updated to `orchestrator.commands.apply.bootstrap_environment` |
| `docs/import-convention.md` | Lazy import pattern documented (Phase 4.5) |

---

## Phase 6 — Docstrings + Types + `__all__`

**Risk:** Low — no behavior change.

| Priority | What | Where |
|----------|------|-------|
| High | Docstrings | `commands/`, `agents/`, public interfaces |
| Medium | Return type annotations | `main.py`, `clients/*.py` |
| Selective | `__all__` | Only `agents/`, `commands/`, `schemas/` |

---

## Phase 7 — Final cleanup

- Verify no circular imports
- `ruff check .` → 0 errors
- `ruff format --check .` → clean
- `pytest` → same results as baseline

---

## Post-refactor — `scanners/quality.py`

Only after all refactoring phases are stable.

- Follows `scanners/python.py` pattern (deterministic, `ast`, `os.walk`)
- Output: Pydantic `QualityReport` consumable by AI agents
- 12 checks across 4 dimensions: readability, complexity, safety, hygiene

---

## Known issues (pre-existing, not introduced by refactoring)

- `runners.run_ruff()` mutates `cmd_override` via `cmd.extend()` when staged
  files exist. Caller-provided list is polluted. Pre-dates extraction from
  original `validator.py` line 195.

---

## QA gates (before every commit)

```bash
ruff check .             # 0 errors
ruff format --check .    # clean
pytest -v                # 332 passed, 2 skipped
```

Commit format: `<type>(<scope>): <message>` (English only)
