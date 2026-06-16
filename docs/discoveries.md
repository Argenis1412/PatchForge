# Technical Debt Discoveries

> Documented during the refactoring process (Phases 0–7).
> None of these block the current refactoring. They are logged here for post-refactor triage.

| # | Issue | Location | Severity | Notes |
|---|-------|----------|----------|-------|
| 1 | Tests import private members (`_logger`, `_cb_validator`) directly | `tests/` (various) | Low | Started in Phase 3. Couples test suite to internal implementation. |
| 2 | `run_ruff()` mutates `cmd_override` via `cmd.extend()` | `agents/validator/runners.py:138` | Medium | Pre-existing. Caller-provided list is polluted. Documented in `refactor-phases.md` Known Issues. |
| 3 | Duplicate run metadata schemas | `schemas/pipeline_run.py` vs `schemas/artifacts.py` | Medium | `PipelineRun` uses `uuid.uuid4()`, `RunMetadata` uses timestamp-based `generate_run_id()`. No single run contract. |
| 4 | Lazy imports in `main.py` lack a formal standard | `main.py` (all CLI commands) | Low | Intentionally lazy, but `docs/import-convention.md` (Phase 4.5) covers it only partially. |
| 5 | `_PROVIDER_CHAIN` dict mutated after definition | `agents/executor/providers.py:32,197` | Low | Defined empty at module level, populated after function defs. Fragile if imported early. |
| 6 | No `QualityReport` schema yet | Post-refactor (`scanners/quality.py`) | — | Planned, not debt. Must not conflict with `ScanFindings` / `ScoutOutput`. |

### Resolved during refactoring

| Issue | Status |
|-------|--------|
| Missing return types in `clients/*.py` | ✅ Fixed in Phase 6 |
| Missing module docstrings across 42 files | ✅ Fixed in Phase 6 |
| Missing `__all__` in public packages | ✅ Fixed in Phase 6 |
