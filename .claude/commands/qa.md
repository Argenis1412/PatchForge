Run the full QA suite and report results in the standard format.

```bash
ruff check .
ruff format --check .
pytest -v
```

Report the results using this exact format:

```
## QA Results

### Ruff
PASS / FAIL

### Format
PASS / FAIL

### Tests
X passed
X failed
X skipped
```

If any check fails, show the full error output and **do not proceed with the commit**.
