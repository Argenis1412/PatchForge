# B8b — State-Machine Worker Loop & Resume

## Goal

Worker loop uses a state machine with per-stage checkpoints for deterministic retry.

---

## Current State

### `src/orchestrator/pipeline.py:40-48` — Pipeline without checkpoint/resume

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

Pipeline runs linearly with no persistence between stages. Crash = full restart. No queue exists — the CLI is invoked directly, never from a webhook.

---

## Changes

### 1. Create the worker loop

In `src/orchestrator/storage/work_queue.py` (append):

```python
import random
from orchestrator.workspace import WorkspaceManager
# import ArtifactStore, GitHubClient, CircuitBreakerOpenError, CircuitBreakerState, etc.

STAGES = ["scout", "architect", "executor", "validator", "apply"]
CHECKPOINT_STAGES = {"scout", "architect", "executor", "validator", "apply"}
BACKOFF_MINUTES = [0, 5, 15]
CB_SLEEP_MAX = 30


def worker_loop(queue_db: Path, coord_db: Path, workspace: WorkspaceManager,
                store: ArtifactStore, github: GitHubClient):
    # Use canonical _sqlite_connect() — never sqlite3.connect() directly.
    # See 00-README.md §Canonical Patterns
    conn_queue = _sqlite_connect(queue_db)
    conn_coord = _sqlite_connect(coord_db)

    while True:
        try:
            if _pre_dequeue_backpressure(conn_coord):
                time.sleep(5)
                continue

            row = dequeue_issue(conn_queue)
            if row is None:
                time.sleep(5)
                continue

            # Idempotency: does a PR already exist for this run_id?
            branch = f"patchforge/run_{row['run_id']}/issue_{row['issue_number']}"
            existing_pr = github.get_pr_for_branch(branch)
            if existing_pr:
                conn_queue.execute(
                    "UPDATE work_queue SET status = 'completed', completed_at = datetime('now') "
                    "WHERE run_id = ?", (row['run_id'],)
                )
                conn_queue.commit()
                continue

            try:
                _execute_pipeline_with_resume(
                    row['run_id'], row['payload'], row['issue_number'], conn_queue, conn_coord,
                    workspace, github, store
                )
                conn_queue.execute(
                    "UPDATE work_queue SET status = 'completed', completed_at = datetime('now') "
                    "WHERE run_id = ?", (row['run_id'],)
                )
            except CircuitBreakerOpenError:
                scheduled = random.randint(15, 45)
                conn_queue.execute(
                    "UPDATE work_queue SET status = 'pending', scheduled_after = "
                    "datetime('now', '+{} seconds') WHERE run_id = ?".format(scheduled),
                    (row['run_id'],)
                )
            except Exception as e:
                if row['retries'] >= 2:
                    conn_queue.execute(
                        "UPDATE work_queue SET status = 'dead_letter', error = ? WHERE run_id = ?",
                        (str(e), row['run_id'])
                    )
                else:
                    backoff = BACKOFF_MINUTES[row['retries']]
                    conn_queue.execute(
                        "UPDATE work_queue SET status = 'pending', retries = retries + 1, "
                        "scheduled_after = datetime('now', '+{} minutes') WHERE run_id = ?"
                        .format(backoff), (row['run_id'],)
                    )
            conn_queue.commit()
        except Exception as outer_e:
            import sys
            sys.stderr.write(f"Worker loop DB or fatal error: {outer_e}\n")
            time.sleep(10)  # Backoff to prevent rapid crash loop


def _pre_dequeue_backpressure(conn_coord: sqlite3.Connection) -> bool:
    """Block if any provider is OPEN."""
    open_providers = conn_coord.execute(
        f"SELECT provider, recovery_timeout FROM cb_state WHERE state = '{CircuitBreakerState.OPEN.value}'"
    ).fetchall()
    if open_providers:
        timeout = min(r["recovery_timeout"] for r in open_providers)
        time.sleep(min(timeout, CB_SLEEP_MAX))
        return True
    return False
```

### 2. Pipeline execution with checkpoint resume

In `src/orchestrator/storage/work_queue.py` (append):

```python
ARTIFACT_MAP = {
    "scout":     ("findings.json",  lambda o: o.model_dump_json()),
    "architect": ("plan.json",      lambda o: o.model_dump_json()),
    "executor":  ("patch.diff",     lambda o: o["patch"]),
    "validator": ("validation.json", lambda o: o.model_dump_json()),
}


def _hydrate_workspace(run_id: str, workspace: WorkspaceManager, store: ArtifactStore) -> Path:
    """Phase 0: Hydrate full workspace from ArtifactStore before state machine resumes."""
    run_dir = workspace.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    # Residual risk: events.jsonl is NOT recovered — append-only log, best-effort durability.
    # Loss on worker recycle is accepted (events are observability, not operational).
    for name in ("run.json", "risk_gate.json"):
        try:
            data = store.read(f"{run_id}/{name}")
            run_dir.joinpath(name).write_text(data)
        except FileNotFoundError:
            pass
    return run_dir


def _execute_pipeline_with_resume(run_id, payload, issue_number, conn, conn_coord, workspace, github, store):
    payload_dict = json.loads(payload)
    _hydrate_workspace(run_id, workspace, store)
    _ensure_clone(payload_dict, workspace)

    for stage in STAGES:
        if stage in CHECKPOINT_STAGES and stage != "apply":
            existing = conn.execute(
                "SELECT output FROM pipeline_checkpoint WHERE run_id=? AND stage=?",
                (run_id, stage)
            ).fetchone()
            if existing:
                _hydrate_stage(run_id, stage, existing[0], workspace, store)
                continue

        if stage == "executor":
            output, patch = run_executor(...)
            ref = store.write(f"{run_id}/patch.diff", patch)
            conn.execute(
                "INSERT OR REPLACE INTO pipeline_checkpoint (run_id, stage, output) "
                "VALUES (?, ?, ?)",
                (run_id, "executor",
                 json.dumps({"ref": ref, "checksum": sha256(patch).hexdigest()}))
            )
            conn.commit()
        elif stage == "apply":
            # issue_number from the queue row is passed through for branch name unification
            _execute_apply_with_checkpoints(run_id, conn, github, workspace, issue_number=row.get("issue_number"))
        else:
            output = _run_stage(stage, ...)
            conn.execute(
                "INSERT OR REPLACE INTO pipeline_checkpoint (run_id, stage, output) "
                "VALUES (?, ?, ?)",
                (run_id, stage, output.model_dump_json())
            )
            conn.commit()


def _execute_apply_with_checkpoints(run_id: str, conn: sqlite3.Connection, github: GitHubClient, workspace: WorkspaceManager, issue_number: Optional[int] = None) -> None:
    """Recovery-safe wrapper for the apply stage using WAL (apply.json) as the source of truth."""
    run_dir = workspace.run_dir(run_id)
    wal_path = run_dir / "apply.json"
    branch = (
        f"patchforge/run_{run_id}/issue_{issue_number}"
        if issue_number
        else f"patchforge/run_{run_id}"
    )
    repo_path = workspace.root  # or target repository path

    if wal_path.exists():
        try:
            wal = ApplyResult.model_validate_json(wal_path.read_text())
        except Exception:
            wal = None

        if wal and wal.status != "applied":
            # WAL-authoritative recovery: roll back remote and local state
            pre_apply_head = wal.pre_apply_head

            if wal.status in ("pr_created", "pushed_remote"):
                if wal.status == "pr_created" and wal.pr_number:
                    try:
                        github.close_pr(wal.pr_number)
                    except Exception:
                        pass
                try:
                    push_delete_remote(repo_path, branch)
                except Exception:
                    pass

            # Safe local cleanup sequence (WAL is the source of truth)
            if pre_apply_head:
                try:
                    force_reset_apply(repo_path, "HEAD")
                    checkout_detached(repo_path, pre_apply_head)
                    delete_local_branch(repo_path, branch, force=True)
                except Exception:
                    pass

    # Run the core apply pipeline
    from orchestrator.commands.apply import execute as execute_apply
    execute_apply(run_id=run_id, workspace=repo_path,
                  issue_number=issue_number,
                  worker_id=os.environ.get("WORKER_ID"),
                  coordination_db_dir=coordination_db_dir)
```

### 3. Logging already has run_dir in V1 stage functions

No changes needed to `pipeline.py` — it is legacy pre-V1 code (never instantiated in production). All V1 stage functions (`scan.py`, `plan.py`, `preview.py`, `apply.py`) already pass `run_dir` / `logs_dir` to `log_event()` and `log_failure()` correctly. The worker loop (Section 1) calls these stage functions directly.

---

## Files to Create/Modify

- `src/orchestrator/storage/work_queue.py` — Append `worker_loop`, `_execute_pipeline_with_resume`, `_execute_apply_with_checkpoints`

---

## Acceptance Criteria

- [ ] Worker crash mid-pipeline → retry hydrates workspace → resumes from last checkpoint
- [ ] Worker crash in Scout → retry reuses checkpointed findings (never re-scans)
- [ ] Worker crash in Architect → retry reuses checkpointed plan (never re-generates)
- [ ] Worker crash in Executor → retry reuses checkpointed patch via URN (never re-executes LLM)
- [ ] Worker crash in Validator → retry reuses checkpointed verdict (never re-validates with LLM)
- [ ] Worker crash in apply → retry sees WAL status and applies the correct rollback
- [ ] Branch has exactly 1 commit: `"PatchForge: {run_id} [skip ci]"`
- [ ] Cross-run_id idempotency: issue_lock prevents duplicate admission; webhook handler checks branch name (`issue_N` in `pr.head.ref`) as second-line recovery
- [ ] Two workers polling simultaneously never process the same issue
- [ ] Orphaned tasks due to silent worker crashes are reclaimed after a 1-hour visibility lease
- [ ] Residual risk of task visibility lease expiration without worker heartbeat is documented (tasks exceeding 1 hour can suffer duplicate processing/split-brain)
- [ ] After 3 failures (non-CB), issue moves to `dead_letter`
- [ ] CB outage (OPEN) → `CircuitBreakerOpenError` yields issue without burning retry
- [ ] CB outage (HALF_OPEN) → reactive probe in provider layer → non-LLM issues dequeued freely; only LLM calls compete for probe slot → `ProbeSlotBusyError` yields issue by 15-45s randomized
- [ ] `run_id` identifies exactly one patch + verdict

---

## Test skeleton (create before running pytest)

Create `tests/test_worker_loop.py` with these cases:
```python
def test_worker_loop_resumes_from_checkpoint():
    """Verify worker_loop skips completed stages by reading pipeline_checkpoint."""
    pass

def test_worker_loop_cb_half_open_yields():
    """Verify ProbeSlotBusyError causes randomized yield without burning retries."""
    pass
```

## Verification

```bash
pytest tests/test_worker_loop.py -v
```

## Rollback

```bash
git checkout -- src/orchestrator/storage/work_queue.py
```
