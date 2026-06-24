# B2 — RunMetadata as Single Source of Truth

## Goal

Make `run.json` the **only** context schema. Workers must be able to reconstruct full execution context from `run.json` alone — no parallel state structures, no `Path.cwd()` dependency, no separate `WorkerContext`.

---

## Current State

### `src/orchestrator/schemas/artifacts.py:63-88` — RunMetadata (too sparse)

```python
class RunMetadata(BaseModel):
    run_id: str
    target_path: str
    workspace_path: str
    base_commit: str
    branch: str
    status: str = "scanning"
    schema_version: int = CURRENT_SCHEMA_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    v1_supported: bool
    support_reasons: List[str] = Field(default_factory=list)
    risk_budget: Literal["low", "medium", "high"] = "low"
    max_files: int = Field(default=2, ge=1)
    max_diff_lines: int = Field(default=100, ge=1)

    goal: Optional[str] = None
    affected_files: Optional[List[str]] = None
    patch_checksum: Optional[str] = None
    validation_summary: Optional[str] = None
    model_metadata: Optional[dict[str, Any]] = None
    lifecycle_state: Optional[PatchLifecycleState] = None
    apply_status: Optional[str] = None
    failure_artifacts: Optional[List[str]] = None
```

Missing: `logs_dir`, `staging_dir`, `trace_id`, `env_file`, `worker_id`, `secrets_ref`, `provider_config`, `current_stage`.

### `src/orchestrator/schemas/pipeline_run.py:34-49` — Parallel PipelineRun (must be removed)

```python
class PipelineRun(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_path: str
    status: str = "pending"
    scout_meta: Optional[AgentMeta] = None
    architect_meta: Optional[AgentMeta] = None
    executor_meta: Optional[AgentMeta] = None
    validator_meta: Optional[AgentMeta] = None
    tasks_total: int = 0
    tasks_applied: int = 0
    tasks_failed: int = 0
    tasks_pending_review: int = 0
    task_results: List[TaskResult] = []
    pending_human_review: List[str] = []
    total_cost_usd: float = 0.0
    finished_at: Optional[datetime] = None
```

`PipelineRun` duplicates routing state that should live in `RunMetadata`. Workers cannot hydrate from `PipelineRun` because it's not persisted to `run.json`.

### `src/orchestrator/pipeline.py:40-48` — Pipeline depends on PipelineRun

```python
class Pipeline:
    def __init__(self, config: TargetConfig, from_stage: str | None = None) -> None:
        self.config = config
        self.target_path = config.target_path
        self.run = PipelineRun(target_path=str(self.target_path))
        self.from_stage = from_stage
        self.trace_id = str(uuid.uuid4())
        self.workspace = WorkspaceManager(self.config.workspace_path)
        self.workspace.setup()
```

`self.run` is a `PipelineRun` — not `RunMetadata`. Worker B cannot hydrate this from disk.

---

## Changes

### 1. Add execution context fields to `RunMetadata`

`src/orchestrator/schemas/artifacts.py`:

```python
class RunMetadata(BaseModel):
    # ... existing fields (keep all) ...

    # Sprint 0 additions — execution context for workers
    issue_number: Optional[int] = None      # GitHub issue number, for branch name unification
    logs_dir: Optional[str] = None
    staging_dir: Optional[str] = None
    trace_id: Optional[str] = None
    env_file: Optional[str] = None
    worker_id: Optional[str] = None
    secrets_ref: Optional[str] = None       # Key to vault/env
    provider_config: Optional[dict] = None   # Provider order, models, timeout
    current_stage: Optional[str] = None      # Stage for state-machine resume
```

### 2. Persist `run.json` to ArtifactStore (via existing write paths)

`Pipeline.execute()` is legacy (pre-V1 monolithic pipeline, never instantiated in production after commands/refactor). The worker loop (B8b) calls stage functions directly. No changes to `pipeline.py` are needed.

The run.json dual-write to ArtifactStore is handled by modifying `WorkspaceManager.write_run_json()` in `workspace.py` (B5 territory):

```python
# In workspace.py (B5), modify write_run_json():
def write_run_json(self, run_id: str, run_metadata: RunMetadata) -> Path:
    ...
    local_path = workspace_mgr.root.joinpath("runs", run_id, "run.json")
    local_path.write_text(serialized, encoding="utf-8")
    # TODO-B5: Uncomment when ArtifactStore exists (sprint-2/02-b5-artifact-store.md).
    # store.write(f"{run_id}/run.json", serialized)
    return local_path
```

### 3. Remove `Path.cwd()` dependencies

`src/orchestrator/commands/apply.py:57-60` currently accepts `workspace` as an optional parameter with fallback to `Path.cwd()`:

```python
if workspace is not None:
    workspace_path = Path(workspace).resolve()
else:
    workspace_path = default_workspace_path()
```

Accept via `PATCHFORGE_WORKSPACE` env var or explicit parameter. Remove the `Path.cwd()` / `default_workspace_path()` fallback for worker contexts.

### 5. Remove `schemas/worker.py` from plan

No separate `WorkerContext` schema will be created. `run.json` is the single source of truth.

---

## Files to Modify

- `src/orchestrator/schemas/artifacts.py` — Add fields to `RunMetadata` (including `issue_number`)
- `src/orchestrator/workspace.py` — Accept PATCHFORGE_WORKSPACE env var

---

## Acceptance Criteria

- [ ] Reading `run.json` alone gives a worker everything needed to cold-start
- [ ] No second context schema exists — `run.json` is the single source of truth
- [ ] No code path relies on `Path.cwd()` for workspace or logs discovery
- [ ] `WorkspaceManager.write_run_json()` persists all context fields (incl. `issue_number`)

---

## Test skeleton (create before running pytest)

Create `tests/test_run_metadata.py` with these cases:
```python
def test_run_metadata_serialization():
    """Verify all new fields serialize/deserialize correctly."""
    pass

def test_run_metadata_issue_number():
    """Verify issue_number round-trips through RunMetadata serialization."""
    pass
```

## Verification

```bash
pytest tests/test_run_metadata.py -v
pytest tests/test_pipeline.py -v
```

Save as `verify_b2.py` and run: `python verify_b2.py`
```python
from orchestrator.schemas.artifacts import RunMetadata
m = RunMetadata(logs_dir='/tmp/logs', staging_dir='/tmp/staging', trace_id='abc', run_id='test', target_path='/tmp', workspace_path='/tmp', base_commit='abc123', branch='main', v1_supported=True)
d = m.model_dump_json(indent=2)
m2 = RunMetadata.model_validate_json(d)
assert m2.logs_dir == '/tmp/logs'
assert m2.trace_id == 'abc'
print('OK')
```

## Rollback

```bash
git checkout -- src/orchestrator/schemas/artifacts.py
git checkout -- src/orchestrator/workspace.py
```
