# B6 — Risk Gate Audit Trail

## Goal

Make risk gate decisions auditable post-hoc. Infrastructure file changes (Dockerfile, CI config, etc.) must never be auto-PR'd — they must always be escalated to high risk.

---

## Current State

### `src/orchestrator/risk.py:8-39` — `check_plan_gate` without dangerous-file heuristic

```python
def check_plan_gate(
    run_metadata: RunMetadata,
    architect_output: ArchitectOutput,
) -> RiskGateResult:
    reasons: list[str] = []
    budget = run_metadata.risk_budget

    for task in architect_output.implementation_plan:
        if task.risk_level == "high":
            reasons.append(
                f"Task {task.task_id} ('{task.title}') is high-risk. "
                "High-risk tasks are not applicable in V1."
            )
        elif task.risk_level == "medium" and budget == "low":
            reasons.append(
                f"Task {task.task_id} ('{task.title}') is medium-risk "
                f"but risk_budget is '{budget}'."
            )

    files = set()
    for task in architect_output.implementation_plan:
        files.update(task.files_to_modify)
    if len(files) > run_metadata.max_files:
        reasons.append(
            f"Plan modifies {len(files)} file(s), exceeding max_files={run_metadata.max_files}."
        )

    return RiskGateResult(
        passed=len(reasons) == 0,
        gate="plan",
        reasons=reasons,
    )
```

No dangerous-file heuristic. A 1-line change to `Dockerfile` could be classified `low-risk` and auto-PR'd without review.

### `src/orchestrator/schemas/risk.py:10-13` — RiskGateResult without persistence

```python
class RiskGateResult(BaseModel):
    passed: bool
    gate: str
    reasons: list[str]
```

No serialization to disk. Result is logged to `events.jsonl` but never persisted as an independent artifact.

---

## Changes

### 1. Persist `RiskGateResult` as `risk_gate.json` artifact

In `src/orchestrator/risk.py`, at the end of `check_plan_gate()` and `check_patch_gate()`:

```python
import json
# Write risk_gate.json via WorkspaceManager (delegates to ArtifactStore after B5)
risk_result = RiskGateResult(passed=len(reasons)==0, gate="plan", reasons=reasons)
workspace_mgr.write_artifact(run_metadata.run_id, "risk_gate.json",
    risk_result.model_dump_json(indent=2))
```

Note: `workspace_mgr` needs to be passed to both functions. `check_patch_gate()` gets `workspace_mgr: Optional[WorkspaceManager] = None` — only persists when provided.

### 2. Add dangerous-file heuristic to `check_plan_gate()`

Add before the existing risk checks:

```python
DANGEROUS_PATTERNS = {
    "Dockerfile", "Makefile", "docker-compose.yml",
    ".github/workflows/", "Jenkinsfile", "requirements.txt",
    "setup.py", "setup.cfg", "pyproject.toml",
}

def _is_dangerous(path: str) -> bool:
    """Match basename OR directory prefix (e.g. .github/workflows/deploy.yml)."""
    p = Path(path)
    if p.name in DANGEROUS_PATTERNS:
        return True
    for parent in p.parents:
        candidate = str(parent) + "/"
        if candidate in DANGEROUS_PATTERNS:
            return True
    return False

for task in architect_output.implementation_plan:
    for f in task.files_to_modify:
        if _is_dangerous(f):
            # NOTE: mutates architect_output in-place — plan.json will reflect escalated risk
            task.risk_level = "high"  # Escalate
            reasons.append(f"File {f} is infrastructure — escalated to high risk")
```

Note: The heuristic lives only in `check_plan_gate()` where `task.files_to_modify` is available. `check_patch_gate()` only sees the raw diff text and cannot reconstruct individual file paths — this is by design.

### 3. Add `risk_gate.json` to `RunMetadata.failure_artifacts` when gate blocks

If the gate doesn't pass, append `"risk_gate.json"` to `run_metadata.failure_artifacts`:

```python
if not risk_result.passed:
    if run_metadata.failure_artifacts is None:
        run_metadata.failure_artifacts = []
    if "risk_gate.json" not in run_metadata.failure_artifacts:
        run_metadata.failure_artifacts.append("risk_gate.json")
```

### 4. Update callers to pass `workspace_mgr`

In `src/orchestrator/commands/plan.py`, pass `workspace_mgr` to `check_plan_gate()`.
In `src/orchestrator/commands/preview.py`, pass `workspace_mgr` to `check_patch_gate()` (not `check_plan_gate()` — preview calls `check_patch_gate()`).

---

## Files to Modify

- `src/orchestrator/risk.py` — Add `_is_dangerous()`, persist `risk_gate.json` via `write_artifact`, update `check_plan_gate()` and `check_patch_gate()` signatures
- `src/orchestrator/schemas/risk.py` — No changes needed (but ensure serialization works)
- `src/orchestrator/commands/plan.py` — Pass `workspace_mgr` to `check_plan_gate()`
- `src/orchestrator/commands/preview.py` — Pass `workspace_mgr` to `check_patch_gate()`

---

## Acceptance Criteria

- [ ] Risk gate decisions are auditable post-hoc via `risk_gate.json`
- [ ] Infrastructure file changes (Dockerfile, CI, etc.) are never auto-PR'd
- [ ] `risk_gate.json` is present in every run directory (not just when gate blocks)

---

## Test skeleton (create before running pytest)

Create `tests/test_risk_gate.py` with these cases:
```python
def test_infrastructure_files_escalate_risk():
    """Verify changes to Dockerfile or pyproject.toml escalate to high risk."""
    pass

def test_risk_gate_json_persisted():
    """Verify risk_gate.json is written to run_dir regardless of pass/fail."""
    pass
```

## Verification

```bash
pytest tests/test_risk_gate.py -v
pytest tests/test_risk_budget.py -v

# Manual: run plan with Dockerfile change, verify risk_gate.json has high risk
patchforge plan --issue 42
cat outputs/staging/run_*/risk_gate.json | python -c "import sys,json; d=json.load(sys.stdin); assert 'Dockerfile' in str(d['reasons']); print('OK')"
```

## Rollback

```bash
git checkout -- src/orchestrator/risk.py
git checkout -- src/orchestrator/commands/plan.py
git checkout -- src/orchestrator/commands/preview.py
git checkout -- src/orchestrator/schemas/risk.py
```
