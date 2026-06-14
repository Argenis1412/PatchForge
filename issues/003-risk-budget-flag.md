---
title: Add --risk-budget flag to `patchforge scan` command
severity: medium
labels: enhancement, cli, risk-gate, dogfooding
---

## Problem

Currently `scan.py` hardcodes:

```python
risk_budget="low",
max_files=2,
max_diff_lines=100,
```

This blocks refactors touching 3+ files. In Experiment 002, a pure
refactor (moving `write_verdict()` from `schemas/experiment.py` to
`WorkspaceManager` in `workspace.py` — no logic change) was blocked
by the risk gate. The workaround was manually editing `run.json` to
set `risk_budget` to `"medium"` and `max_files` to `5`.

## Required Changes

**Three files** need modification:

### 1. `src/orchestrator/main.py` (line ~117)

Add `--risk-budget` Typer option to the `scan` command function:

```python
risk_budget: Optional[str] = typer.Option(
    None,
    "--risk-budget",
    help="Risk budget: 'low', 'medium', or 'high'",
)
```

Validate value is one of `low`, `medium`, `high` (case-sensitive). On
invalid value, print error with valid options and `raise typer.Exit(1)`.

Pass `risk_budget` to `execute_scan(config=config, risk_budget=risk_budget)`.

### 2. `src/orchestrator/commands/scan.py` (line 27, lines 127-141)

Change `execute()` signature to:

```python
def execute(config: TargetConfig, risk_budget: str | None = None) -> None:
```

Map `risk_budget` to concrete values at the `RunMetadata` construction
site (lines 127-141):

| `risk_budget`  | risk_budget field | max_files | max_diff_lines |
|----------------|-------------------|-----------|----------------|
| `None`         | `"low"`           | `2`       | `100`          |
| `"low"`        | `"low"`           | `2`       | `100`          |
| `"medium"`     | `"medium"`        | `5`       | `250`          |
| `"high"`       | `"high"`          | `10`      | `500`          |

### 3. `tests/test_scan.py` — 3 new tests

- **`test_scan_default_risk_budget`** — No `--risk-budget` flag →
  `run.json` has `risk_budget: "low"`, `max_files: 2`
- **`test_scan_medium_risk_budget`** — `--risk-budget medium` →
  `run.json` has `risk_budget: "medium"`, `max_files: 5`
- **`test_scan_invalid_risk_budget`** — `--risk-budget invalid` →
  exit code 1, error message showing valid options

## Code Style Constraints

The target repo uses `ruff line-length = 100`. All generated code:

- Must not exceed **100 characters per line** (E501)
- Must pass `ruff check .` with 0 errors
- Error messages in test assertions and implementation must be **verbatim identical**
- Use `assert "<partial message>" in result.output` for error assertions (not exact string match)

## Scope

- `src/orchestrator/main.py` — CLI option + validation + pass-through
- `src/orchestrator/commands/scan.py` — accept param, apply to RunMetadata
- `tests/test_scan.py` — 3 new tests
- No changes to `risk.py`, `artifacts.py`, or `config.py`
- `max_files`/`max_diff_lines` defaults unchanged (backward compatible)

## Acceptance Criteria

- `patchforge scan . --risk-budget medium` writes `risk_budget: "medium"`,
  `max_files: 5`, `max_diff_lines: 250` in `run.json`
- Without flag, behavior is identical to current (backward compatible)
- Invalid `--risk-budget` value prints error with valid options and exits 1
- `ruff check .` — 0 errors
- `pytest` — all existing tests pass
- 3 new tests cover: default, explicit flag, invalid flag

## Non-goals

- No changes to `check_plan_gate()` or `check_patch_gate()` in `risk.py`
- No changes to scheduler, provider fallback, or `TargetConfig`
- No auto-escalation logic based on experiment history or change type
