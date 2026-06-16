# Import binding convention

When `main.py` delegates to a `commands/*.py` module, always import the
function inside the Typer command body (lazy import), not at module level.

## Correct — lazy import inside function body

```python
@app.command()
def apply(...):
    from orchestrator.commands.apply import execute as execute_apply
    execute_apply(...)
```

This pattern is already used by existing commands:

| Command   | `main.py` line |
|-----------|----------------|
| `scan()`  | 124            |
| `plan()`  | 149            |
| `preview()` | 163          |
| `doctor()` | 44           |

## Wrong — eager import at module level

```python
# DON'T do this in main.py:
from orchestrator.commands.apply import execute as execute_apply  # BAD

@app.command()
def apply(...):
    execute_apply(...)
```

## Why

`monkeypatch.setattr("orchestrator.commands.apply.execute", mock)` replaces the
attribute on the module dictionary. If `main.py` imported the function at module
level, its local reference still points to the original object and the mock
takes no effect. Tests pass silently against the wrong code path.

Lazy imports inside function bodies resolve the attribute reference at call
time, so `monkeypatch` and `patch()` work correctly.
