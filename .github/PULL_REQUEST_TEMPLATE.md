## Changes Description
This PR closes **Phase 2: Quality & Testing** of epic #13 (Stabilizing Agent-Lab). The main goal was to transform the test suite from manual validation scripts to a professional suite based on `pytest` with deterministic asserts and mocks, ensuring the stability of the contract between agents.

## Key Changes
- **Pytest Configuration**: Enabled `[tool.pytest.ini_options]` in `pyproject.toml` with `pythonpath = ["."]`.
- **Testing Infrastructure**: Created `tests/conftest.py` with fixtures for SDK isolation.
- **Test Suite**: Implemented 5 test modules (`test_scout`, `test_architect`, `test_executor`, `test_validator`, `test_schemas`) replacing the legacy `test_signatures.py`.
- **Quality**: Included `QUALITY_GATE.md` as a mandatory pre-merge checklist.
- **Resolution of Blockers**: Created `schemas/pipeline_run.py` to fix import errors in the pipeline.

## Validation Checklist
- [x] `ruff check .` passes without errors.
- [x] `pytest tests/ -m unit` (complete suite operative).
- [x] Schema `pipeline_run` implemented and tested.
- [x] Removed technical debt of `test_signatures.py`.

## Impact
Substantial improvement in the detectability of contract failures between agents. Any future changes that break Pydantic schemas will be detected immediately in CI.
