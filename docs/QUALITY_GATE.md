# Quality Gate — Pre-Merge Checklist

## Mandatory commands (all must pass)

### 1. Lint
```bash
ruff check .
```

### 2. Type check (schemas)
```bash
python -c "from orchestrator.schemas.scout_output import ScoutOutput; from orchestrator.schemas.architect_output import ArchitectOutput; from orchestrator.schemas.executor_output import ExecutorOutput; from orchestrator.schemas.validator_output import ValidatorOutput; print('OK')"
```

### 3. Unit tests
```bash
pytest tests/ -m unit -v
```

## Approval criteria
- [ ] `ruff check .` → 0 errors
- [ ] `pytest tests/ -m unit` → 100% pass
- [ ] No `print("PASS")` or `print("FAIL")` in tests
- [ ] Every agent returns `tuple[Output, dict]` with full metadata
