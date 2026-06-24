# P3 Migration Plan: Async Workers & CI/CD Integration

**Status:** Draft  
**Branch:** `feat/p3-migration-plan`  
**Date:** 2026-06-17  
**Based on:** P0-P2 completion (Phases 0-7, Issue Contracts, Experiment 001-004)

> **Implementation prompts available at `docs/planning/p3/`**  
> Each Blocker is split into an atomic prompt file for Claude Code Pro:  
> - `sprint-0/` — B1 (WAL), B2 (RunMetadata), B6 (Risk Gate)  
> - `sprint-1/` — B4 (Circuit Breaker), B7 (Workspace Isolation)  
> - `sprint-2/` — B8 (Work Queue), B3 (GitHub), B5 (Artifact Store)  
> See `docs/planning/p3/00-README.md` for execution order.

---

## Executive Summary

P3 (Async Workers & CI/CD Integration) is **not ready** for implementation as-is. The current architecture is deeply coupled to a single-process, single-repo, local-filesystem model. Four critical blockers and four moderate issues must be resolved before Docker workers or GitHub integration can function reliably.

**Current state:** PatchForge CLI monousuario works (Exp-001, -002, -003, -004 verified).  
**Target state:** Workers receive issues, clone repos, execute pipeline, open PRs.  
**Gap:** 8 discrete changes required — 4 critical, 4 moderate.

---

## Blockers Overview

| ID | Blocker | Severity | Effort | Sprint |
|----|---------|----------|--------|--------|
| **B1** | Distributed rollback lacks intermediate checkpoints | 🔴 Critical | Medium | Sprint 0 |
| **B2** | Execution state coupled to CLI process — not serializable for workers | 🔴 Critical | High | Sprint 0 |
| **B3** | No GitHub API integration (webhook, PR, rate limits) | 🔴 Critical | High | Sprint 2 |
| B4 | Circuit breakers are in-process singletons (no sharing between workers) | 🟡 Moderate | Medium | Sprint 1 |
| B5 | No artifact persistence beyond local filesystem | 🟡 Moderate | Medium | Sprint 2 |
| B6 | Risk gates lack audit trail and dangerous-file heuristics | 🟡 Moderate | Low | Sprint 0 |
| B7 | No workspace isolation or repository locking for concurrent workers | 🟡 Moderate | Low | Sprint 1 |
| **B8** | No work queue — at-least-once delivery impossible | 🔴 Critical | Medium | Sprint 2 |

---

## Sprint 0 — Foundation (Pre-P3 Prerequisites)

**Goal:** Make the pipeline worker-safe without changing the CLI experience.

### B1 — Write-Ahead Log (WAL) for Atomic Apply

**Evidence:**  
`commands/apply.py:157` saves a single checkpoint (`pre_apply_head`). `apply_patch()` at line 183 applies the entire `patch.diff` in one `git apply` call. If the worker dies between successful apply and `apply.json` write, the repo is in an unrecoverable mixed state. `rollback.py:10` requires a target SHA but no pre-apply diff is preserved.

**Files to modify:**
- `src/orchestrator/commands/apply.py`
- `src/orchestrator/agents/executor/rollback.py`
- `src/orchestrator/git.py`

**Changes:**

1. Write `apply.json` with `status: "applying"` **directly to filesystem** (WAL bypasses ArtifactStore — atomicity requires local fs):
   ```python
   # Before git operations — local filesystem only, never delegate to ArtifactStore
   apply_result = ApplyResult(
       run_id=run_id, applied_at=datetime.now(timezone.utc),
       branch=branch_name, success=False, status="applying",
       pre_apply_head=pre_apply_head, pre_apply_branch=pre_apply_branch,
   )
   tmp = run_dir / "apply.json.tmp"
   tmp.write_text(apply_result.model_dump_json(indent=2), encoding="utf-8")
   tmp.stat()  # force OS flush
   os.replace(tmp, run_dir / "apply.json")  # atomic on POSIX, near-atomic on Windows
   ```

2. Preserve `patch.diff` in a known location before applying:
    ```python
    import shutil
    backup_path = run_dir / "patch.apply-backup.diff"
    shutil.copy2(patch_path, backup_path)
    ```

3. `git apply` → `git commit -m "PatchForge: {run_id} [skip ci]"` → `git push origin branch`.

4. Update `rollback.py` to accept an optional backup diff and attempt `git apply --reverse` before `force_reset_apply`.

5. Extend rollback for each crash point during apply — checkpoint status guides Worker B's recovery (P3 target is Docker ephemeral — `committed_local` is no-op):

    | Crash point | Checkpoint status | Rollback (persistent) | Rollback (Docker ephemeral) |
    |-------------|-------------------|----------------------|----------------------------|
    | After `git apply`, before `git commit` | `applying` | `git reset --hard pre_apply_head` | `git reset --hard pre_apply_head` |
    | After `git commit`, before `git push` | `committed_local` | `git reset --hard pre_apply_head` + `git branch -D branch` | **No-op** (commit lost with container) |
    | After `git push`, before `create_pr` | `pushed_remote` | `git push origin --delete branch` | `git push origin --delete branch` |
    | After `create_pr` | `pr_created` | `gh pr close` + `git push origin --delete branch` | `gh pr close` + `git push origin --delete branch` |

**ACs:**
- Worker crash mid-apply leaves repo in detectable "applying" state (WAL + pipeline_checkpoint)
- `patch.apply-backup.diff` exists for manual recovery
- Rollback works from backup diff even if `force_reset_apply` fails
- `git log patchforge/run_{run_id} ^main` returns exactly 1 commit — the PatchForge commit

---

### B2 — RunMetadata as Single Source of Truth

**Evidence:**  
`RunMetadata` (artifacts.py) lacks `logs_dir`, `staging_dir`, `trace_id`, `env_file`. `PipelineRun` (pipeline_run.py) is a parallel state structure with in-memory timings. Workers have no way to reconstruct execution context from disk alone. A separate `WorkerContext` schema would create two sources of truth — `run.json` must be sufficient.

**Files to modify:**
- `src/orchestrator/schemas/artifacts.py` (RunMetadata)
- `src/orchestrator/workspace.py` (WorkspaceManager)
- `src/orchestrator/pipeline.py` (Pipeline)

**Changes:**

1. Add all execution context fields to `RunMetadata` (eliminates need for separate `WorkerContext`):
   ```python
   class RunMetadata(BaseModel):
       # ... existing fields ...
       # Sprint 0 additions — execution context for workers
       logs_dir: Optional[str] = None
       staging_dir: Optional[str] = None
       trace_id: Optional[str] = None
       env_file: Optional[str] = None
       worker_id: Optional[str] = None
        secrets_ref: Optional[str] = None       # Key to vault/env
        provider_config: Optional[dict] = None   # Provider order, models, timeout
        current_stage: Optional[str] = None      # Stage for state-machine resume (A10)
    ```

2. `Pipeline.execute()` always populates `logs_dir`, `staging_dir`, `trace_id` on write AND persists `run.json` to the ArtifactStore (`store.write(f"{run_id}/run.json", run_json)`) so Worker B can hydrate it during `_hydrate_workspace()`.

3. Remove dependency on `Path.cwd()` for workspace resolution in `commands/apply.py:61` — accept via env var `PATCHFORGE_WORKSPACE` or explicit parameter.

4. Remove `schemas/worker.py` from plan — not needed.

**ACs:**
- Reading `run.json` alone gives a worker everything needed to cold-start
- No second context schema exists — `run.json` is the single source of truth
- No code path relies on `Path.cwd()` for workspace or logs discovery

---

### B6 — Risk Gate Audit Trail

**Evidence:**  
`RiskGateResult` is logged to `events.jsonl` but never persisted as an independent artifact. No heuristic flags dangerous file types (Dockerfile, setup.py, CI config). A 1-line change to `Dockerfile` could be classified `low-risk` and auto-PR without review.

**Files to modify:**
- `src/orchestrator/risk.py`
- `src/orchestrator/schemas/risk.py`
- `src/orchestrator/commands/plan.py`
- `src/orchestrator/commands/preview.py`

**Changes:**

1. Persist `RiskGateResult` as `risk_gate.json` artifact:
   ```python
   workspace_mgr.write_artifact(run_id, "risk_gate.json", risk_result.model_dump_json(indent=2))
   ```

2. Add dangerous-file heuristic to `check_plan_gate()`:
   ```python
   DANGEROUS_FILES = {"Dockerfile", "Makefile", "docker-compose.yml",
                      ".github/workflows/", "Jenkinsfile", "requirements.txt",
                      "setup.py", "setup.cfg", "pyproject.toml (build section)"}
   for task in architect_output.implementation_plan:
       for f in task.files_to_modify:
           if any(d in f for d in DANGEROUS_FILES):
               task.risk_level = "high"  # Escalate
               reasons.append(f"File {f} is infrastructure — escalated to high risk")
   ```

3. Add `risk_gate.json` to `RunMetadata.failure_artifacts` when gate blocks.

**ACs:**
- Risk gate decisions are auditable post-hoc
- Infrastructure file changes are never auto-PR'd
- `risk_gate.json` is present in every run directory

---

## Sprint 1 — Distribution Primitives

**Goal:** Enable safe concurrent execution across workers.

### B4 — Externalized Circuit Breaker State

**Evidence:**  
`circuit_breaker.py:222` uses a process-level `_registry: dict`. Each Docker worker initializes its own CB instances in CLOSED state at import time. A Gemini outage burns 3 failed calls per worker before all CBs open. No exponential backoff.

**Files to modify:**
- `src/orchestrator/circuit_breaker.py`
- `src/orchestrator/agents/executor/providers.py`

**Changes:**

1. Extract CB persistence to an adapter interface:
   ```python
   class CircuitBreakerStore(ABC):
       @abstractmethod
       def get_state(self, provider: str) -> Optional[dict]: ...
       @abstractmethod
       def set_state(self, provider: str, state: dict) -> None: ...
       @abstractmethod
       def atomic_update(self, provider: str, txn: Callable) -> bool: ...
   ```

2. Implement `SqliteCircuitBreakerStore` (zero dependencies, uses `coordination.db` — shared with B7 repo_lock):
    ```python
    class SqliteCircuitBreakerStore(CircuitBreakerStore):
        def __init__(self, db_dir: Path):
            self._conn = sqlite3.connect(str(db_dir / "coordination.db"))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("CREATE TABLE IF NOT EXISTS cb_state ("
                               "provider TEXT PRIMARY KEY,"
                               "state TEXT NOT NULL,"
                               "failures INTEGER DEFAULT 0,"
                               "last_failure_at REAL,"
                               "recovery_timeout REAL)")
    ```
    SQLite WAL mode enables concurrent reads from multiple workers. Write contention is negligible at ≤10 workers. For larger deployments the `CircuitBreakerStore` interface supports a Redis adapter. CB and lock share `coordination.db` so a corruption there is auto-recoverable (CB resets to CLOSED, locks expire by TTL) and never impacts the queue.

3. Add exponential backoff to recovery:
   ```python
   RECOVERY_BACKOFF = [60, 120, 240, 480, 900]  # 1min → 15min cap
   ```

4. Update `_recoverable_exceptions` in providers.py to catch timeout-based exceptions for proper CB recording.

5. Add reactive HALF_OPEN probe in the provider call layer — probe token is acquired only when the worker is about to actually call the LLM, not before dequeuing:

    ```python
    # NOTE: The implementation (commits e81fafe, ac978c7) diverged from this spec.
    # _call_with_half_open_probe, ProbeSlotBusyError, _release_probe_token,
    # and _cleanup_stale_probes were removed. Instead:
    #   - CircuitBreaker.call() is the sole production gatekeeper
    #   - SqliteCircuitBreakerStore is injected into circuit_breaker_for()
    #   - _reload_state() reads shared SQLite state on every call()
    #   - time.time() replaces time.monotonic() for restart-safe persistence
    #   - _half_open_in_flight is process-local only; cross-worker contention is accepted
    #   - half_open_probe table exists in coordination.db but is UNUSED by production code
    ```

**ACs:**
- CB state survives worker restart — `SqliteCircuitBreakerStore` persists to `coordination.db`
- A Gemini outage opens CB globally — `_reload_state()` on each `call()` reads latest state from shared SQLite
- Exponential backoff prevents thundering herd on recovery (60s → 900s cap)
- Cross-worker HALF_OPEN contention is NOT prevented — accepted relaxation (first probe success resets CB for all)

---

### B7 — Workspace Isolation & Repository Locking

**Evidence:**  
`workspace.py:48` staging is shared under `outputs/staging/<run_id>/`. No PID file, no lock. Two workers hitting the same repo clone collide in staging writes.

**Files to modify:**
- `src/orchestrator/workspace.py`
- `src/orchestrator/git.py` (add optional lock mechanism)

**Changes:**

1. Incorporate `worker_id` into workspace paths:
   ```python
   # WorkspaceManager.__init__
   self.root = root / worker_id  # e.g., .../workspaces/<hash>/worker-abc123
   ```
   Workers are physically isolated by clone — branches are unique per run_id/issue_number.
   `repo_lock` is OPTIONAL and DISABLED by default (`REPO_LOCK_ENABLED=False`).

2. Add optional repo lock using `coordination.db` (only enabled for shared-NFS deployments):
    ```python
    # storage/lock.py — repo_lock table in coordination.db
    def acquire_repo_lock(repo_identity: str, worker_id: str, ttl_seconds: int = 300) -> bool:
        conn = sqlite3.connect(str(db_dir / "coordination.db"))  # same file as CB
        conn.execute("BEGIN IMMEDIATE")  # serializes all workers
       try:
           row = conn.execute(
               "SELECT worker_id, expires_at FROM repo_lock WHERE repo = ?",
               (repo_identity,)
           ).fetchone()
           now = time.time()
           if row:
               if now < row["expires_at"] and row["worker_id"] != worker_id:
                   return False  # actively locked by another worker
               # stale (TTL expired) or self-owned → update
           conn.execute(
               "INSERT OR REPLACE INTO repo_lock (repo, worker_id, expires_at) VALUES (?, ?, ?)",
               (repo_identity, worker_id, now + ttl_seconds)
           )
           conn.commit()
           return True
       except Exception:
           conn.rollback()
           return False
   ```
   `BEGIN IMMEDIATE` guarantees mutual exclusion across all workers — SQLite serializes write transactions at the process level. No TOCTOU, no stale lock races. Works on Docker volumes because the `.db` file is on a shared bind mount.

3. Create `repo_lock` and `pipeline_checkpoint` tables alongside CB and queue tables:
   ```sql
   CREATE TABLE repo_lock (
       repo TEXT PRIMARY KEY,
       worker_id TEXT NOT NULL,
       expires_at REAL NOT NULL
   );

CREATE TABLE pipeline_checkpoint (
        run_id TEXT NOT NULL,
        stage TEXT NOT NULL CHECK(stage IN ('scout','architect','executor','validator','apply')),
        output TEXT NOT NULL,  -- serialized JSON (lightweight) or URN (executor patch)
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (run_id, stage)
    );
   ```

4. Add `half_open_probe` table to coordination.db for CB probe slot (exactly 1 worker during HALF-OPEN):

    ```sql
    CREATE TABLE half_open_probe (
        provider TEXT PRIMARY KEY,
        worker_id TEXT NOT NULL,
        acquired_at TEXT NOT NULL  -- datetime; cleanup stale > 5min
    );
    ```

    **Implementation note:** This table is created by `SqliteCircuitBreakerStore.__init__()` but is **unused** by production code. The accepted relaxation is that cross-worker HALF_OPEN contention is not prevented.

5. Stale lock TTL is enforced by the `expires_at` check — no separate cleanup needed. The store handles it transactionally.

5. Cleanup TTL: `WorkspaceManager.cleanup_stale_workspaces(max_age_hours=24)`.

**ACs:**
- Two workers never write to the same staging directory
- Repo lock prevents simultaneous `git apply` on the same clone — no TOCTOU race possible
- Stale locks are reaped transactionally (TTL check in `BEGIN IMMEDIATE`)
- Two stores for blast radius isolation: `coordination.db` (B4 cb_state + B7 repo_lock + half_open_probe) and `queue.db` (B8 work_queue + pipeline_checkpoint + issue_lock). A corruption in one never blocks the other.

---

## Sprint 2 — CI/CD Surface

**Goal:** Workers produce PRs against GitHub repositories.

**Prerequisite:** B8 and B3 share the `QueuePayload` schema (defined in Sprint 1.5) and the same SQLite store. They are implemented in parallel against these shared contracts. B8's worker loop depends on `GitHubClient.get_pr_for_branch()`, which is part of B3 — define the method signature in Sprint 1.5 to unblock parallel development.

### B8 — Work Queue (at-least-once delivery)

**Evidence:**  
The document described *what* workers do but not *how* they receive work. Without a queue:
- Worker crash during pipeline execution loses the issue forever (no retry)
- Two workers can pick up the same issue simultaneously (no ack/dead-letter)
- No backpressure mechanism if all workers are busy

**Queue architecture:**

```
Issue → Webhook → Queue → Worker → PR
```

Queue backend: SQLite — `queue.db` is a separate file from `coordination.db` (B4/B7) so a CB corruption never impacts work delivery. Both files live under `stores/` on the same Docker volume. `queue.db` also holds `issue_lock` table for admission idempotency (single-DB transaction guarantees ACID).

```sql
CREATE TABLE issue_lock (
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    locked_until TEXT,           -- TTL: auto-releases after 1h (worker phantom)
    PRIMARY KEY (repo, issue_number)
);

CREATE TABLE work_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_number INTEGER NOT NULL,
    repo TEXT NOT NULL,
    run_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|processing|completed|failed|dead_letter
    created_at TEXT NOT NULL,
    retries INTEGER DEFAULT 0,
    payload TEXT NOT NULL,  -- JSON: issue body, labels, etc.
    started_at TEXT,
    completed_at TEXT,
    scheduled_after TEXT,   -- backoff: NULL on first attempt, set after failure
    error TEXT
);
```

**Enqueue (single-DB transaction — no split-brain between lock and queue):**

```python
def enqueue_issue(conn_queue: sqlite3.Connection, issue: Issue) -> Optional[str]:
    """Admission idempotency via issue_lock + enqueue in one ACID transaction."""
    try:
        conn_queue.execute("BEGIN IMMEDIATE")
        run_id = generate_run_id()
        # issue_lock prevents duplicate admission even with webhook re-delivery
        conn_queue.execute(
            "INSERT INTO issue_lock (repo, issue_number, run_id) "
            "VALUES (?, ?, ?)",
            (issue.repo, issue.number, run_id)
        )
        conn_queue.execute(
            "INSERT INTO work_queue (run_id, issue_number, repo, status, "
            "created_at, payload) VALUES (?, ?, ?, 'pending', datetime('now'), ?)",
            (run_id, issue.number, issue.repo, issue.body)
        )
        conn_queue.commit()
        return run_id
    except sqlite3.IntegrityError:
        conn_queue.rollback()
        return None  # duplicate — discard webhook silently
```

**Worker loop (state machine with per-stage checkpoints — preserves run_id ↔ patch bijection):**

```python
STAGES = ["scout", "architect", "executor", "validator", "apply"]
CHECKPOINT_STAGES = {"scout", "architect", "executor", "validator", "apply"}

BACKOFF_MINUTES = [0, 5, 15]
CB_SLEEP_MAX = 30

def _pre_dequeue_backpressure(conn_coord: sqlite3.Connection):
    """Block if any provider is OPEN. HALF_OPEN handled reactively in provider layer."""
    open_providers = conn_coord.execute(
        "SELECT provider, recovery_timeout FROM cb_state WHERE state = 'OPEN'"
    ).fetchall()
    if open_providers:
        timeout = min(r["recovery_timeout"] for r in open_providers)
        time.sleep(min(timeout, CB_SLEEP_MAX))
        return True
    return False

def worker_loop(queue_db: Path, coord_db: Path,
                github: GitHubClient, workspace: WorkspaceManager,
                store: ArtifactStore):
    conn = sqlite3.connect(str(queue_db))    # queue.db
    conn_coord = sqlite3.connect(str(coord_db))  # coordination.db

    while True:
        # Backpressure: check CB + probe slot before dequeuing
        if _pre_dequeue_backpressure(conn_coord):
            continue

        row = conn.execute("""
            UPDATE work_queue SET status = 'processing', started_at = datetime('now')
            WHERE id = (
                SELECT id FROM work_queue
                WHERE status = 'pending'
                  AND (scheduled_after IS NULL OR scheduled_after <= datetime('now'))
                ORDER BY created_at ASC
                LIMIT 1
            )
            RETURNING run_id, issue_number, repo, payload, retries
        """).fetchone()
        conn.commit()  # Commit the claim immediately — don't hold a write txn during the pipeline

        if row is None:
            time.sleep(5); continue

        # Idempotency: does a PR with this run_id branch already exist?
        branch = f"patchforge/run_{row.run_id}/issue_{row.issue_number}"
        existing_pr = github.get_pr_for_branch(branch)
        if existing_pr:
            conn.execute("UPDATE work_queue SET status = 'completed', "
                         "completed_at = datetime('now') WHERE run_id = ?", (row.run_id,))
            conn.commit()
            continue

        try:
            _execute_pipeline_with_resume(row.run_id, row.payload,
                                          conn, conn_coord, workspace, github, store)
            conn.execute("UPDATE work_queue SET status = 'completed', "
                         "completed_at = datetime('now') WHERE run_id = ?", (row.run_id,))
        except (CircuitBreakerOpenException, ProbeSlotBusy):
            # CB open or HALF_OPEN slot busy — yield issue, don't burn retry
            scheduled = random.randint(15, 45)
            conn.execute(
                "UPDATE work_queue SET status = 'pending', "
                "scheduled_after = datetime('now', '+{} seconds') WHERE run_id = ?"
                .format(scheduled), (row.run_id,))
        except Exception as e:
            if row.retries >= 2:
                conn.execute("UPDATE work_queue SET status = 'dead_letter', "
                             "error = ? WHERE run_id = ?", (str(e), row.run_id))
            else:
                backoff = BACKOFF_MINUTES[row.retries]
                conn.execute(
                    "UPDATE work_queue SET status = 'pending', retries = retries + 1, "
                    "scheduled_after = datetime('now', '+{} minutes') WHERE run_id = ?"
                    .format(backoff), (row.run_id,))
        conn.commit()


def _hydrate_workspace(run_id: str, workspace: WorkspaceManager, store: ArtifactStore) -> Path:
    """Phase 0: Hydrate full workspace from ArtifactStore.
    Ensures run_dir, run.json, events.jsonl, risk_gate.json exist on disk
    before the state machine resumes."""
    run_dir = workspace.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("run.json", "events.jsonl", "risk_gate.json"):
        try:
            data = store.read(f"{run_id}/{name}")
            run_dir.joinpath(name).write_text(data)
        except FileNotFoundError:
            pass  # not yet written — first attempt
    return run_dir

# NOTE: _release_probe_token and _cleanup_stale_probes were removed in the actual
# B4 implementation (commits e81fafe, ac978c7) along with _call_with_half_open_probe.
# CircuitBreaker.call() + SqliteCircuitBreakerStore is the sole mechanism.

def _ensure_clone(payload: dict, workspace: WorkspaceManager) -> Path:
    """Clone repo if not present in worker's workspace."""
    repo_path = workspace.root / "repo"
    if not (repo_path / ".git").exists():
        git_clone(payload["repo_url"], repo_path)
    return repo_path

ARTIFACT_MAP = {
    "scout":     ("findings.json",  lambda o: o.model_dump_json()),
    "architect": ("plan.json",      lambda o: o.model_dump_json()),
    "executor":  ("patch.diff",     lambda o: o["patch"]),
    "validator": ("validation.json", lambda o: o.model_dump_json()),
}

def _hydrate_stage(run_id: str, stage: str, output_json: str,
                   workspace: WorkspaceManager, store: ArtifactStore):
    """Re-materialize physical artifacts from checkpoint output.
    Lightweight outputs (scout, architect, validator) → inline JSON → write to disk.
    Heavy output (executor) → URN in checkpoint → download from ArtifactStore."""
    name, extractor = ARTIFACT_MAP[stage]
    data = json.loads(output_json)
    if stage == "executor":
        # executor output is a URN + checksum — download from ArtifactStore
        ref = data["ref"]
        patch_content = store.read(ref)
        workspace.run_dir(run_id).joinpath(name).write_text(patch_content)
    else:
        workspace.run_dir(run_id).joinpath(name).write_text(extractor(data))

def _execute_pipeline_with_resume(run_id: str, payload: str,
                                   conn: sqlite3.Connection,
                                   conn_coord: sqlite3.Connection,
                                   workspace: WorkspaceManager,
                                   github: GitHubClient,
                                   store: ArtifactStore) -> None:
    """Execute pipeline as state machine. Resume from last checkpoint on retry.
    Preserves run_id ↔ patch bijection across worker crashes."""
    import json

    # Phase 0: hydrate workspace from ArtifactStore before any skip
    payload_dict = json.loads(payload)
    _hydrate_workspace(run_id, workspace, store)
    _ensure_clone(payload_dict, workspace)

    for stage in STAGES:
        # Generic skip — excludes apply (handles its own phases internally)
        if stage in CHECKPOINT_STAGES and stage != "apply":
            existing = conn.execute(
                "SELECT output FROM pipeline_checkpoint WHERE run_id=? AND stage=?",
                (run_id, stage)
            ).fetchone()
            if existing:
                # Hydrate artifacts before skipping (workspace must match)
                _hydrate_stage(run_id, stage, existing[0], workspace, store)
                continue

        if stage == "scout":
            output = run_scout(...)
        elif stage == "architect":
            output = run_architect(...)
        elif stage == "executor":
            output, patch = run_executor(...)
            # Write patch to ArtifactStore, store only URN in checkpoint
            ref = store.write(f"{run_id}/patch.diff", patch)
            conn.execute(
                "INSERT OR REPLACE INTO pipeline_checkpoint (run_id, stage, output) "
                "VALUES (?, ?, ?)",
                (run_id, "executor",
                 json.dumps({"ref": ref, "checksum": sha256(patch).hexdigest()}))
            )
            conn.commit()
        elif stage == "validator":
            output = run_validator(...)
        elif stage == "apply":
            import json
            pre_apply_head = git.get_head_sha()
            existing = conn.execute(
                "SELECT output FROM pipeline_checkpoint WHERE run_id=? AND stage=?",
                (run_id, "apply")
            ).fetchone()
            status = json.loads(existing[0])["status"] if existing else None

            if status == "applied":
                continue  # complete — skip

            # Cleanup for phases that reached remote
            if status in ("pr_created", "pushed_remote"):
                if status == "pr_created":
                    pr_num = json.loads(existing[0]).get("pr_number")
                    if pr_num:
                        github.close_pr(pr_num)
                git_push_delete_remote(branch)

            # committed_local + applying: no-op in Docker (commit lost with container)
            # Fall through to re-execute apply from scratch

            # Phase 1: about to git_commit
            conn.execute(
                "INSERT OR REPLACE INTO pipeline_checkpoint (run_id, stage, output) "
                "VALUES (?, ?, ?)",
                (run_id, "apply",
                 '{"status":"applying","pre_apply_head":"' + pre_apply_head + '"}')
            )
            conn.commit()
            git_checkout_branch(branch)
            git_apply(patch_path)
            git_commit(message=f"PatchForge: {run_id} [skip ci]")
            # Phase 2: committed locally, about to push
            conn.execute(
                "INSERT OR REPLACE INTO pipeline_checkpoint (run_id, stage, output) "
                "VALUES (?, ?, ?)",
                (run_id, "apply",
                 '{"status":"committed_local","pre_apply_head":"' + pre_apply_head + '"}')
            )
            conn.commit()
            git_push(remote="origin", branch=branch)
            # Phase 3: pushed, about to create_pr
            conn.execute(
                "INSERT OR REPLACE INTO pipeline_checkpoint (run_id, stage, output) "
                "VALUES (?, ?, ?)",
                (run_id, "apply",
                 '{"status":"pushed_remote","pre_apply_head":"' + pre_apply_head + '"}')
            )
            conn.commit()
            pr = github.create_pr(head=branch,
                                  title=f"PatchForge: {goal[:50]}",
                                  body=assemble_pr_body(run_dir, issue_number))
            # Phase 4: PR created, about to finalize
            conn.execute(
                "INSERT OR REPLACE INTO pipeline_checkpoint (run_id, stage, output) "
                "VALUES (?, ?, ?)",
                (run_id, "apply",
                 '{"status":"pr_created","pre_apply_head":"' + pre_apply_head
                 + '","pr_number":' + str(pr.number) + '}')
            )
            conn.commit()
            # Phase 5: complete — overwrites pr_created
            conn.execute(
                "INSERT OR REPLACE INTO pipeline_checkpoint (run_id, stage, output) "
                "VALUES (?, ?, ?)",
                (run_id, "apply",
                 '{"status":"applied","pre_apply_head":"' + pre_apply_head
                 + '","pr_number":' + str(pr.number) + '}')
            )
            conn.commit()

        # Checkpoint after non-deterministic stages into shared SQLite store
        # (executor and apply handle their own checkpoints internally)
        if stage in CHECKPOINT_STAGES and stage not in ("executor", "apply"):
            conn.execute(
                "INSERT OR REPLACE INTO pipeline_checkpoint "
                "(run_id, stage, output) VALUES (?, ?, ?)",
                (run_id, stage, output.model_dump_json())
            )
            conn.commit()  # durable on shared Docker volume

    # PR already created inside the apply stage (lines 677-688).
    # No second create_pr call here — it would violate exactly-once guarantee.
```

**Properties guaranteed:**
- **At-least-once delivery:** Work moves to `processing` atomically — two workers cannot claim the same issue
- **Exactly-once admission:** `issue_lock` table with `UNIQUE(repo, issue_number)` prevents duplicate enqueue even with webhook re-delivery (single-DB ACID transaction)
- **Exactly-once PR output (intra-run_id):** Idempotency via branch name (`patchforge/run_{run_id}/issue_{issue_number}`) using linearizable GitHub PR API (`/pulls?head=`)
- **Exactly-once PR output (cross-run_id):** Branch name encodes issue_number. Webhook handler checks `issue_N` in `pr.head.ref` via `GET /pulls?state=open` — no duplicate even after queue.db recovery. Branch name is immutable (not editable by UI).
- **run_id ↔ result bijection preserved:** Retry reads `pipeline_checkpoint` table in `queue.db`. Worker B runs `_hydrate_workspace()` before any skip to materialize git clone + run.json + artifacts from ArtifactStore. Apply's 5-phase checkpoint guides exact rollback.
- **Retry with backoff:** 0 → 5 → 15 minutes before 3rd attempt → dead_letter
- **CB-aware backpressure:** `CircuitBreakerOpenError` does NOT count as a retry. Pre-dequeue check blocks workers when any provider is OPEN. `scheduled_after` yields by 15-45s (randomized) on HALF_OPEN. `CircuitBreaker.call()` with `_reload_state()` from shared SQLite is the sole production gatekeeper.
- **Backpressure:** If all workers are busy, issues stay `pending` in the queue

**Files to modify:**
- `src/orchestrator/storage/work_queue.py` (NEW — work_queue + pipeline_checkpoint + issue_lock tables in `queue.db`)
- `src/orchestrator/storage/lock.py` (NEW — repo_lock table in `coordination.db`)
- `src/orchestrator/clients/bootstrap.py` (init both stores: `coordination.db` → cb_state + repo_lock; `queue.db` → work_queue + pipeline_checkpoint + issue_lock)

**ACs:**
- Worker crash mid-pipeline → retry hydrates workspace via `_hydrate_workspace()` → resumes from last checkpoint via state machine → same patch, same verdict, same PR
- Worker crash in Scout → retry reuses checkpointed findings (never re-scans)
- Worker crash in Architect → retry reuses checkpointed plan (never re-generates)
- Worker crash in Executor → retry reuses checkpointed patch via URN (never re-executes LLM)
- Worker crash in Validator → retry reuses checkpointed verdict (never re-validates with LLM)
- Worker crash in apply (after git_commit, before git_push) → retry sees `committed_local` → no-op in Docker (commit lost with container) → re-execute apply from `git_apply`
- Worker crash after git_push, before create_pr → retry sees `pushed_remote` → `git push origin --delete branch` + re-execute
- Worker crash after create_pr, before final conn.commit() → retry sees `pr_created` → close PR + `git push origin --delete branch` + re-execute
- Worker crash after final conn.commit() → retry sees `applied` → skip (PR + branch already exist)
- Branch has exactly 1 commit: `"PatchForge: {run_id} [skip ci]"`
- Cross-run_id idempotency: issue_lock prevents duplicate admission; webhook handler checks branch name (`issue_N` in `pr.head.ref`) as second-line recovery
- Duplicate webhook delivery → `IntegrityError` on `issue_lock` → discard silently (no duplicate PR, no double LLM cost)
- CB outage (OPEN) → workers detect OPEN via pre-dequeue check → sleep until recovery → 0 retries burned, 0 issues dead-lettered
- CB outage (HALF_OPEN) → reactive probe in provider layer → non-LLM issues dequeued freely; only LLM calls compete for probe slot → `ProbeSlotBusy` yields issue by 15-45s randomized
- Two workers polling simultaneously never process the same issue
- After 3 failures (non-CB), issue moves to `dead_letter` and a human is notified
- `run_id` identifies exactly one patch + verdict — verified by executing the full pipeline twice and comparing patch checksums
- Queue is observable via SQL queries

---

### B3 — GitHub Integration

**Evidence:**  
Zero GitHub API code exists. `.github/workflows/ci.yml` is standard pytest, not a PatchForge worker. No webhook handler, no PR creation, no issue detection, no rate limit handling.

**New files:**
- `src/orchestrator/clients/github.py`
- `src/orchestrator/integrations/` (package)
- `src/orchestrator/integrations/webhook.py`

**Changes:**

1. GitHub API client (`clients/github.py`):
    ```python
    class GitHubClient:
        def __init__(self, token: str, repo: str): ...
        def get_issue(self, issue_number: int) -> Issue: ...
        def get_pr_for_branch(self, branch: str) -> Optional[PR]: ...  # GET /pulls?head= — linearizable
        def list_open_pulls(self) -> List[PR]: ...  # GET /pulls?state=open — for cross-run_id idempotency
        def create_pr(self, title: str, body: str, head: str, base: str) -> PR: ...
        def update_pr(self, pr_number: int, body: str) -> None: ...
        def comment_on_issue(self, issue_number: int, body: str) -> None: ...
        def add_label(self, issue_number: int, label: str) -> None: ...
        def update_pr_body(self, pr_number: int, body: str) -> None: ...
    ```

2. Idempotency via branch name (source of truth is git — branch is immutable, PR body is mutable):
    ```python
    BRANCH_TEMPLATE = "patchforge/run_{run_id}/issue_{issue_number}"
    PR_TITLE_TEMPLATE = "PatchForge: {goal[:50]}"
    COMMIT_TEMPLATE = "PatchForge: {run_id} [skip ci]"
    ```
    Two-layer idempotency:
    - **Intra-run_id:** Worker loop calls `github.get_pr_for_branch(branch)` using the linearizable `/pulls?head=` API before executing. Branch includes issue_number so a matching branch always maps to the same issue.
    - **Cross-run_id (recovery):** Webhook handler calls `_existing_pr_for_webhook(issue_number)` before enqueueing — iterates open PRs and checks `f"issue_{issue_number}"` in `pr.head.ref` (branch name is immutable, not editable by UI). If a PR already exists from a prior run (different run_id but same issue), the webhook is discarded. No GitHub label or PR body is used as a guard.

3. GitHub labels for display/observability only (never read as idempotency check):
   ```python
   PATCHFORGE_LABELS = {
       "patchforge/pending": "Awaiting processing",
       "patchforge/processing": "Currently being processed",
       "patchforge/ready": "PR ready for review",
       "patchforge/failed": "Pipeline execution failed",
   }
   ```
   These are updated asynchronously after the SQLite transaction commits. If a label update fails, the worker continues — labels are cosmetic, not operational.

4. Webhook handler (`integrations/webhook.py`):
    - Accept `issues.opened` events
    - Ignore cosmetic fast-path labels (not a real guard)
    - **Cross-run_id idempotency (hot path):** call `github._existing_pr_for_webhook(issue_number)` — iterates open PRs, checks branch name (`issue_{N}` in `pr.head.ref`). O(1) API call. Branch name is immutable — no PR body editing can break it.
    - Call `enqueue_issue()` which generates `run_id` and inserts into SQLite queue
    - Return immediately — worker loop handles the rest

    ```python
    def _existing_pr_for_webhook(self, issue_number: int) -> Optional[PR]:
        """Hot path (queue.db sano). O(1), state=open, parses branch name (immutable)."""
        for pr in self.list_open_pulls():  # GET /pulls?state=open, 1 call
            if pr.head.ref and f"issue_{issue_number}" in pr.head.ref:
                return pr
        return None

    def _existing_pr_for_recovery(self, issue_number: int) -> Optional[PR]:
        """Recovery path only (queue.db corrupto).
        Operator-triggered webhook re-delivery. Pagination tolerated."""
        page = 1
        while page <= 3:
            for pr in self.list_pulls(state="all", per_page=100, page=page):
                if pr.head.ref and f"issue_{issue_number}" in pr.head.ref:
                    return pr
            page += 1
        return None
    ```

5. PR body assembly with size guard:
   ```python
   MAX_PR_BODY_BYTES = 120_000  # 5KB safety margin under GitHub's 125KB limit
   def assemble_pr_body(run_dir: Path, goal: str, run_id: str) -> str:
       parts = []
       # Summary always fits
       parts.append(f"## PatchForge: {goal}")
       parts.append(f"**Run ID:** {run_id}")
       # Artifacts as GitHub markdown links (not inline)
       # Full diff goes as a PR comment if too large
   ```
   > **Note:** The final signature in B5 adds `store_base_url` for URL-based artifact references.
   > This snippet is a simplified sketch — see B5 for the production implementation.

5. Rate limit handling:
   ```python
   def call_with_retry(method: Callable, max_retries=3):
       for attempt in range(max_retries):
           try:
               return method()
           except GithubException as e:
               if e.status == 403 and "rate limit" in str(e).lower():
                   wait = int(e.headers.get("Retry-After", 60))
                   time.sleep(wait + random.uniform(0, 5))  # jitter
                   continue
               raise
   ```

**ACs:**
- `patchforge plan --issue 42` opens a PR with patch.diff, validation.json, verdict
- Same issue processed twice (intra-run_id) → `get_pr_for_branch` finds existing branch → skips
- Same issue after queue.db recovery (cross-run_id) → `_existing_pr_for_webhook` finds PR via `issue_N` in branch name → discards webhook → no duplicate
- Branch has exactly 1 commit: `"PatchForge: {run_id} [skip ci]"`
- Labels are display-only: if label update fails, pipeline execution is unaffected
- PR body respects GitHub API size limits (truncation with warning)
- Rate limit backoff with jitter prevents secondary rate limiting

---

### B5 — Pluggable Artifact Store

**Evidence:**  
All artifacts (`patch.diff`, `validation.json`, `apply.json`) are written to the local workspace via `workspace.py:90`. In Docker, the filesystem is ephemeral. No blob storage. GitHub PR body must reference artifacts via URL, not inline them.

**Design constraint:** Storage is a persistence concern, not a schema concern. The `ArtifactStore` interface lives in the `storage/` layer, separate from `schemas/artifacts.py` which defines only data shapes.

**New files:**
- `src/orchestrator/storage/__init__.py`
- `src/orchestrator/storage/artifact_store.py` (ABC interface)
- `src/orchestrator/storage/local_store.py` (default implementation)

**Files to modify:**
- `src/orchestrator/workspace.py`

**Changes:**

1. Define artifact backend interface in `storage/artifact_store.py` with explicit durability contract:
   ```python
   from enum import Enum, auto

   class DurabilityLevel(Enum):
       LOCAL_ATOMIC = auto()    # os.replace + fsync — for WAL/apply.json
       REMOTE_CONFIRMED = auto()  # S3 200 OK with x-amz-request-id
       REMOTE_EVENTUAL = auto()   # async, best-effort (never for operational data)

   class WriteResult(BaseModel):
       ref: str                       # URL, URN, or path
       durability: DurabilityLevel    # what the caller can rely on

   class ArtifactStore(ABC):
       @abstractmethod
       def write(self, path: str, data: str) -> WriteResult: ...
       @abstractmethod
       def read(self, ref: str) -> str: ...
       @abstractmethod
       def delete(self, ref: str) -> None: ...
   ```
   **Contract:** `write()` must not return until the store confirms durability at the declared level. S3 implementation waits for `200 OK` with `x-amz-request-id`. Local implementation uses `os.replace()` + `stat()`. The WAL (`apply.json`) always bypasses the store and writes directly to local filesystem — it is never delegated.

2. Implement `LocalArtifactStore` (current behavior — default for CLI, path-based URNs, `LOCAL_ATOMIC`).

3. Future: `S3ArtifactStore` — writes to S3/MinIO, returns signed URLs (`REMOTE_CONFIRMED`).

4. PR body assembly becomes a pure markdown formatting function that references external urls:
   ```python
   def assemble_pr_body(run_dir: Path, store_base_url: str, goal: str, run_id: str) -> str:
       return f"""
   ## PatchForge: {goal}

   **Run ID:** {run_id}

   ### Artifacts
   - [patch.diff]({store_base_url}/{run_id}/patch.diff)
   - [validation.json]({store_base_url}/{run_id}/validation.json)
   - [apply.json]({store_base_url}/{run_id}/apply.json)
   - [risk_gate.json]({store_base_url}/{run_id}/risk_gate.json)
   """
   ```
   For the first iteration, `store_base_url` points to a mounted Docker volume or an S3 bucket. GitHub Releases are not used as a storage backend — GitHub is integration, not storage.

5. Make `WorkspaceManager.write_artifact` delegate to the configured store:
   ```python
   def write_artifact(self, run_id: str, name: str, data: str) -> str:
       return self.store.write(f"{run_id}/{name}", data)
   ```

**ACs:**
- Artifacts survive worker container destruction
- PR body references artifacts by URL (never inlined)
- Local store remains default for CLI usage (backward compatible)
- `ArtifactStore` is in `storage/`, not `schemas/` — clean layering

---

## Migration Sequence

```
Sprint 0 (Foundation - Pre-P3)
├── B1: WAL atomic apply          [~2 days]
├── B2: RunMetadata (single source of truth) [~3 days]
├── B6: Risk gate audit           [~1 day]
└── Test: all existing tests pass + new B1/B2 tests
    └── Dogfood: run Exp-001 scenario with context serialization

Sprint 1 (Distribution - P3 Enabler)
├── B4: Externalized CB store (SQLite)  [~2 days]
├── B7: Workspace isolation + repo_lock (same SQLite store) [~1 day]
├── Two-store SQLite schema: `coordination.db` (cb_state + repo_lock), `queue.db` (work_queue + pipeline_checkpoint + issue_lock) [~0.5 days]
└── Test: 2 concurrent runs on same repo + lock contention
    └── Dogfood: run Exp-002 scenario with CB shared store + repo lock

Sprint 1.5 (Schema alignment — boundary between Sprint 1 and 2)
└── QueuePayload schema defined (Sprint 2's B3 and B8 share this contract)

Sprint 2 (CI/CD Surface - P3 Core)
├── B8: Work queue + state-machine worker loop + hydration + CB backpressure (work_queue + pipeline_checkpoint + issue_lock in queue.db) [~3 days]
├── B3: GitHub client + webhook   [~5 days]
├── B5: Pluggable artifact store  [~2 days]
├── Dockerfile + entrypoint       [~1 day]
└── Test: end-to-end issue → queue → worker → PR
    └── Dogfood: PatchForge processes its own issue via worker
    └── Verify: retry produces same patch (checkpoint), no duplicate PRs

P3 Complete
└── `patchforge ci --issue 42` opens PR with full audit trail
```

---

## Verification Plan

| Stage | How to verify | Exit criterion |
|-------|--------------|----------------|
| Sprint 0 | `pytest tests/` — 0 failures | All existing tests pass |
| Sprint 0 | Kill worker mid-apply, restart, check `apply.json` | Status is "applying", rollback possible |
| Sprint 1 | Two workers run same issue concurrently | No staging directory collisions; repo lock serializes git apply |
| Sprint 1 | Kill Gemini API access, watch CB open globally | 1st worker opens CB → 2nd worker sees OPEN via shared SQLite |
| Sprint 1 | Simulate repo lock stale (delete worker container) | Next worker acquires lock via TTL expiry in `BEGIN IMMEDIATE` |
| Sprint 2 | `patchforge ci --issue 42` on test repo | PR exists with diff + validation + verdict |
| Sprint 2 | Process same issue twice | Second run finds existing PR via branch name (`/pulls?head=`), skips |
| Sprint 2 | Kill worker in Scout, verify retry | Retry resumes from `pipeline_checkpoint` → findings match first attempt |
| Sprint 2 | Kill worker in Architect, verify retry | Retry resumes from `pipeline_checkpoint` → plan matches first attempt |
| Sprint 2 | Kill worker in Executor, verify retry | Retry resumes from `pipeline_checkpoint` → patch checksum matches first attempt |
| Sprint 2 | Kill worker in Validator, verify retry | Retry resumes from `pipeline_checkpoint` → verdict matches first attempt |
| Sprint 2 | Kill worker after git_commit, before git_push | Retry sees `committed_local` checkpoint → `git reset --hard` + `git branch -D branch` → re-executes apply cleanly |
| Sprint 2 | Kill worker after git_push, before create_pr | Retry sees `pushed_remote` checkpoint → `git push origin --delete branch` → re-executes apply |
| Sprint 2 | Kill worker after create_pr | Retry sees `pr_created` checkpoint → close PR + delete branch → re-executes apply |
| Sprint 2 | Kill worker mid-pipeline, verify hydration | `_hydrate_workspace()` restores run.json + git clone + artifacts from ArtifactStore before resume |
| Sprint 2 | Duplicate webhook delivery (re-delivery during inference) | `issue_lock` → `IntegrityError` → discard. No duplicate run_id, no double LLM cost |
| Sprint 2 | Webhook re-delivery after queue.db corruption | `_existing_pr_for_webhook()` finds PR via `issue_N` in branch name → discards webhook → no duplicate PR |
| Sprint 2 | CB outage (Gemini down 30 min) while 100 issues pending | Workers detect OPEN via pre-dequeue check → sleep → 0 retries burned, 0 dead-lettered. Non-LLM issues (`apply`) dequeued freely without probe contention |
| Sprint 2 | CB outage recovers to HALF_OPEN, verify probe works | `CircuitBreaker.call()` with `_reload_state()` sees HALF_OPEN from shared SQLite; process-local `_half_open_in_flight` prevents double-probe in same worker; cross-worker contention accepted; first success resets to CLOSED |
| Sprint 2 | Backoff test: fail an issue 3 times consecutively | Retry at +0min, +5min, +15min → dead_letter after 3rd |
| Sprint 2 | Colaborator edits PR body removing `#issue_number` | No effect — idempotency check uses branch name (`pr.head.ref`), not PR body |
| Sprint 2 | Full retry idempotency | Execute pipeline twice, kill at every stage, compare final patch checksums — all identical |

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `os.rename()` not atomic on Windows | Low | Use `shutil.move` + fallback copy; document Windows limitation |
| SQLite shared store (CB + lock + queue) becomes bottleneck at 50+ workers | Low | All three use pluggable interfaces — swap to Redis per deployment scale |
| GitHub API secondary rate limiting | Medium | Add `X-Poll-Interval` header parsing; exponential backoff up to 30min |
| Docker volume permissions on shared SQLite .db file | Medium | Document required volume mounts; run container as non-root UID matching host |
| Queue dead-letter accumulates unnoticed | Low | Add healthcheck endpoint exposing dead-letter count |
| Retry generates different patch under same `run_id` | **None** (design closed) | `pipeline_checkpoint` table in shared SQLite store — each LLM stage runs at most once per `run_id`; Validator and Apply are deterministic |
| `queue.db` corruption with in-flight issues | Low | **Recovery:** Operator re-delivers webhooks from GitHub Settings (30-day retention). `_existing_pr_for_webhook()` via branch name discards issues with existing PR. Maximum loss: N stage checkpoints. `issue_lock` is lost with queue.db — recreated on re-enqueue (IntegrityError does not apply because there are no duplicates in recovery). `PRAGMA journal_mode=WAL` + daily backup |
| `coordination.db` corruption | Low | CB resets to CLOSED (auto-recovered in mins), repo locks expire by TTL (max 5min). No human recovery needed |
| CB open + long outage → workers idle but queue intact | Low | Pre-dequeue OPEN check blocks workers; HALF_OPEN handled by `CircuitBreaker.call()` with `_reload_state()`. `CircuitBreakerOpenError` doesn't burn retries. 0 issues lost per outage. |
| repo_lock enabled unnecessarily | Low | Default is `REPO_LOCK_ENABLED=False`. Workspace isolation + unique branches guarantee no collisions. |
| Shared SQLite store checkpoint corruption | Low | `PRAGMA journal_mode=WAL` for crash recovery; daily `.db` backup via cron; checkpoint table is INSERT-ONLY (no UPDATE if stage already exists — `INSERT OR REPLACE` is atomic, corruption only loses at most one stage) |

---

## Appendix: File Change Matrix

| File | Sprint 0 | Sprint 1 | Sprint 1.5 | Sprint 2 |
|------|----------|----------|------------|----------|
| `commands/apply.py` | WAL, fsync | — | — | — |
| `git.py` | backup diff, git_commit, git_push, git_push_delete_remote | — | — | — |
| `rollback.py` | reverse-apply from backup, git reset per crash-point table | — | — | — |
| `schemas/artifacts.py` | RunMetadata + secrets_ref, provider_config, current_stage | — | — | — |
| `schemas/risk.py` | persist gate result | — | — | — |
| `risk.py` | dangerous-file heuristics | — | — | — |
| `workspace.py` | atomic writes (os.replace) | worker isolation | — | store delegation |
| `circuit_breaker.py` | — | store interface, backoff | — | — |
| `providers.py` | — | DB-backed CB + reactive HALF_OPEN probe | — | — |
| `storage/__init__.py` | — | — | — | NEW (two-store init: coordination.db + queue.db) |
| `storage/artifact_store.py` | — | — | — | NEW (ABC) |
| `storage/local_store.py` | — | — | — | NEW |
| `storage/lock.py` | — | NEW (CircuitBreakerStore ABC + SqliteCircuitBreakerStore; half_open_probe table created but unused) | — | repo_lock added |
| `storage/work_queue.py` | — | — | — | NEW (work_queue + pipeline_checkpoint + issue_lock tables in queue.db, state-machine worker loop with hydration + CB backpressure + scheduled_after backoff) |
| `clients/bootstrap.py` | — | (table creation handled by SqliteCircuitBreakerStore.__init__) | — | NEW (init queue.db: work_queue, pipeline_checkpoint, issue_lock) |
| `schemas/queue_payload.py` | — | — | NEW (QueuePayload contract) | — |
| `clients/github.py` | — | — | define `get_pr_for_branch` signature | NEW (implementation) |
| `integrations/webhook.py` | — | — | — | NEW |
| `integrations/__init__.py` | — | — | — | NEW |
| Dockerfile | — | — | — | NEW |
| `pyproject.toml` | — | — | — | add `PyGithub` dep |

**Total new code:** ~1,550 lines across all sprints  
**Total changed files:** ~20  
**Estimated total effort:** 29-33 engineering days
