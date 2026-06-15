# Summary

Phase 0 of the codebase refactoring project. Establishes a verifiable baseline of quality metrics, behavior characterization tests, and structural measurements before any extraction or restructuring begins. This is a read-only phase — zero changes to existing code.

# Changes

- Add characterization tests for offline CLI commands (`doctor`, `scan`) using `CliRunner`
- Capture QA baseline outputs: `ruff check`, `ruff format --check`, `pytest` (330 pass, 2 skip)
- Capture coverage report (90% total)
- Add `scripts/baseline_metrics.py` — deterministic AST-based structural metrics generator
- Generate `docs/baseline/metrics.json` with real structural measurements
- Add `docs/planning/refactor-phases.md` with full 7-phase refactoring roadmap

# Files Modified

None — all files are new additions.

# Files Added

- `tests/test_baseline_cli.py` — 10 characterization tests for `doctor` and `scan`
- `scripts/baseline_metrics.py` — AST-based structural metrics generator
- `docs/baseline/ruff_check.txt` — baseline output: `ruff check .`
- `docs/baseline/ruff_format.txt` — baseline output: `ruff format --check .`
- `docs/baseline/pytest.txt` — baseline output: `pytest --tb=short -v`
- `docs/baseline/coverage.txt` — baseline output: `coverage report --show-missing`
- `docs/baseline/metrics.json` — structural metrics (file sizes, function sizes, docstrings, type annotations)
- `docs/planning/refactor-phases.md` — phased refactoring roadmap (7 phases + post-refactor quality scanner)

# Before

No baseline existed. Impossible to objectively measure regression during refactoring. Structural metrics were hand-estimated (and incorrect — e.g., executor.py was estimated at 549 lines, actual is 683).

# After

Every refactoring phase can be verified against a known baseline:

| Metric | Value |
|--------|-------|
| ruff check | 0 errors |
| ruff format --check | Clean (80 files) |
| pytest | 330 passed, 2 skipped |
| Coverage | 90% |
| Files >500 lines | 6 |
| Functions >100 lines | 11 |
| Functions >50 lines | 30 |
| Public funcs no docstring | 350 |
| Public funcs no type annotation | 343 |

Characterization tests guard CLI behaviour (exit codes, structural text output, generated artifacts) without depending on exact stdout snapshots.

# Testing

- `ruff check .` → All checks passed (0 errors)
- `ruff format --check .` → 80 files already formatted (clean)
- `pytest --tb=short -v` → 330 passed / 0 failed / 2 skipped
- `coverage report --show-missing` → 90% total

# Acceptance Criteria

- [x] `ruff check .` returns 0 errors
- [x] `ruff format --check .` returns clean
- [x] `pytest` returns 330 passed, 2 skipped (same as baseline + 10 new char tests)
- [x] `scripts/baseline_metrics.py` generates `docs/baseline/metrics.json` deterministically
- [x] Characterization tests pass for `doctor` and `scan` (offline commands)
- [x] All QA outputs saved under `docs/baseline/`
- [x] Refactoring roadmap documented in `docs/planning/refactor-phases.md`
