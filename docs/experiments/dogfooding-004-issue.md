# Issue: Extend `_is_dangerous` to detect requirements file variants

## Severity
low

## Problem

`_is_dangerous` in `src/orchestrator/risk.py` already detects `requirements.txt`
via `DANGEROUS_PATTERNS`, but does not catch common variants used in real projects:

- `requirements-dev.txt`
- `requirements-test.txt`
- `requirements.in` (pip-tools source file)
- `requirements-dev.in`

These files control dependency installation and are infrastructure-sensitive.
They should be flagged as dangerous for the same reason `requirements.txt` is.

The function already handles Dockerfile and docker-compose variants using prefix/suffix
logic. `requirements` variants should follow the same pattern.

## Acceptance Criteria

- [ ] `_is_dangerous("requirements-dev.txt")` returns `True`
- [ ] `_is_dangerous("requirements-test.txt")` returns `True`
- [ ] `_is_dangerous("requirements.in")` returns `True`
- [ ] `_is_dangerous("requirements-dev.in")` returns `True`
- [ ] `_is_dangerous("requirements.txt")` still returns `True` (no regression)
- [ ] `_is_dangerous("src/requirements-dev.txt")` returns `True` (nested path)
- [ ] Safe files like `src/main.py` and `README.md` are NOT affected
- [ ] `ruff check .` passes with 0 errors
- [ ] `pytest tests/` passes with same count or more

## Files to Change

- `src/orchestrator/risk.py` — extend `_is_dangerous` to detect `requirements*.txt`
  and `requirements*.in` variants using the same pattern already used for
  `Dockerfile.*` and `docker-compose.*`

## Files NOT to Change

- `src/orchestrator/commands/apply.py`
- `src/orchestrator/commands/preview.py`
- `src/orchestrator/commands/plan.py`
- `src/orchestrator/commands/scan.py`
- `tests/` (do not delete existing tests)
