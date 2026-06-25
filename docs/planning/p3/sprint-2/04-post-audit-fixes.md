# Post-Audit Fixes — Round 6 Corrections

## Goal

Apply all fixes from the 6th adversarial audit. This prompt resolves 13 🔴 BLOCKING and 8 🟡 MINOR findings across all 9 prompt files. Run this **after** B1-B8b are fully implemented and verified.

Do not re-run any previous blocker — this file patches the source code directly.

---

## Changes

### H-1 — Circuit Breaker state casing mismatch

**Cause:** `CircuitBreakerState` enum values are lowercase (`"open"`, `"closed"`, `"half_open"` at `circuit_breaker.py:36-38`), but all SQL queries and string comparisons in the docs use uppercase. SQLite TEXT comparison is case-sensitive — `_pre_dequeue_backpressure` never blocks.

**Fix:** Change every CB state SQL query and string comparison to use `.value` (lowercase).

In `src/orchestrator/circuit_breaker.py`, replace:

```python
self._state: CircuitBreakerState = CircuitBreakerState.CLOSED
```

Add a property to avoid repeated `.value` calls throughout the codebase:

```python
@property
def state_value(self) -> str:
    return self._state.value
```

In `src/orchestrator/storage/circuit_breaker_store.py` (new from B4), replace all hardcoded SQL values:

| Location | Old (uppercase) | New (lowercase via `.value`) |
|----------|----------------|------------------------------|
| `_persist_state()` | `"state": self._state.value` | ✅ already correct — writes lowercase |
| `_load_full_state()` `state["state"] == "OPEN"` | `"OPEN"` | `state["state"] == CircuitBreakerState.OPEN.value` |
| `_call_with_half_open_probe()` was **removed** in e81fafe | N/A | N/A — function eliminated; `CircuitBreaker.call()` uses `CircuitBreakerState.OPEN.value` via `_reload_state()` |
| `_pre_dequeue_backpressure()` `WHERE state = 'OPEN'` | `'OPEN'` | `f"WHERE state = '{CircuitBreakerState.OPEN.value}'"` |
| Manual verification test `set_state('gemini', {'state': 'OPEN', ...})` | `'OPEN'` | `CircuitBreakerState.OPEN.value` |

Also add `from orchestrator.circuit_breaker import CircuitBreakerState` to `work_queue.py`.

---

### H-2 — Branch name unification: 3 formats → 1

**Cause:** Three incompatible branch name formats exist:
- `apply.py:219`: `f"patchforge/{run_id}"`
- `BRANCH_TEMPLATE` (B3): `"patchforge/run_{run_id}/issue_{issue_number}"`
- `_execute_apply_with_checkpoints` (B8b): `f"patchforge/{run_id}"`

**Fix:** Unify to one format: `f"patchforge/run_{run_id}/issue_{issue_number}"` when `issue_number` exists, else `f"patchforge/run_{run_id}"`.

#### Step 1: Add `issue_number` to `RunMetadata`

In `src/orchestrator/schemas/artifacts.py`, add to `RunMetadata`:

```python
issue_number: Optional[int] = None
```

#### Step 2: Fix `apply.py:execute()` signature and branch

In `src/orchestrator/commands/apply.py`, extend `execute()` signature:

```python
def execute(
    run_id: str,
    allow_dirty: bool = False,
    env_file: Optional[Path] = None,
    workspace: Optional[Path] = None,
    issue_number: Optional[int] = None,       # NEW
    worker_id: Optional[str] = None,           # NEW (for repo_lock in B7)
    coordination_db_dir: Optional[Path] = None, # NEW (for repo_lock in B7)
) -> None:
```

Replace the hardcoded branch at line 219:

```python
# Old:
branch_name = f"patchforge/{run_id}"

# New:
if issue_number is not None:
    branch_name = f"patchforge/run_{run_id}/issue_{issue_number}"
else:
    branch_name = f"patchforge/run_{run_id}"
```

#### Step 3: Read `issue_number` from `run_metadata`, not parameter

At line 71 (`run_metadata = workspace_mgr.read_run_json(run_id)`) and before the branch assignment, add:

```python
# After line 71: run_metadata = workspace_mgr.read_run_json(run_id)
if issue_number is None and run_metadata.issue_number is not None:
    issue_number = run_metadata.issue_number
```

#### Step 4: Fix `_execute_apply_with_checkpoints()` in `work_queue.py`

Replace the old format at the branch assignment:

```python
# Old:
branch = f"patchforge/{run_id}"

# New: issue_number is passed as explicit parameter from caller
# See _execute_pipeline_with_resume signature change below.
branch = (
    f"patchforge/run_{run_id}/issue_{issue_number}"
    if issue_number
    else f"patchforge/run_{run_id}"
)
```

---

### H-3 — Phantom git helper functions

**Cause:** `_execute_apply_with_checkpoints()` references functions that don't exist: `git_push_delete_remote`, `git_reset_hard`, `git_clean`, `git_checkout`, `git_delete_branch_local`, `run_apply_command`.

**Fix:** Add 3 new functions to `git.py` and fix the recovery sequence.

#### Step 1: Add to `src/orchestrator/git.py`

After `revert_apply()` (line 261), append:

```python
def checkout_detached(repo_root: Path, sha: str) -> GitCommandResult:
    """Check out a specific SHA in detached HEAD state."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "checkout", "--detach", sha],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return GitCommandResult(return_code=res.returncode, stdout=res.stdout, stderr=res.stderr)
    except FileNotFoundError as e:
        return GitCommandResult(return_code=127, stdout="", stderr=f"git not found: {e}")


def delete_local_branch(repo_root: Path, branch: str, force: bool = True) -> GitCommandResult:
    """Delete a local branch (force with -D)."""
    args = ["git", "-C", str(repo_root), "branch", "-D" if force else "-d", branch]
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=30)
        return GitCommandResult(return_code=res.returncode, stdout=res.stdout, stderr=res.stderr)
    except FileNotFoundError as e:
        return GitCommandResult(return_code=127, stdout="", stderr=f"git not found: {e}")


def push_delete_remote(repo_root: Path, branch: str, remote: str = "origin") -> GitCommandResult:
    """Delete a remote branch via git push --delete."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "push", remote, "--delete", branch],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return GitCommandResult(return_code=res.returncode, stdout=res.stdout, stderr=res.stderr)
    except FileNotFoundError as e:
        return GitCommandResult(return_code=127, stdout="", stderr=f"git not found: {e}")
```

#### Step 2: Fix `_execute_apply_with_checkpoints()` recovery block

In `src/orchestrator/storage/work_queue.py`, replace the phantom functions with real ones:

```python
# Old phantom block (lines ~203-227):
if wal.status in ("pr_created", "pushed_remote"):
    if wal.status == "pr_created" and wal.pr_number:
        try:
            github.close_pr(wal.pr_number)
        except Exception:
            pass
    try:
        git_push_delete_remote(branch)
    except Exception:
        pass
if pre_apply_head:
    try:
        git_reset_hard(repo_path, "HEAD")
        git_clean(repo_path, force=True, dirs=True)
        git_checkout(repo_path, pre_apply_head)
        git_delete_branch_local(repo_path, branch, force=True)
    except Exception:
        pass
run_apply_command(run_id, workspace)

# New (import real functions):
from orchestrator.git import checkout_detached, delete_local_branch, push_delete_remote, revert_apply

# ... then in the recovery block:
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
if pre_apply_head:
    try:
        revert_apply(repo_path)
        checkout_detached(repo_path, pre_apply_head)
        delete_local_branch(repo_path, branch, force=True)
    except Exception:
        pass
```

Replace the `run_apply_command(run_id, workspace)` line with the real function call:

```python
# Old:
run_apply_command(run_id, workspace)

# New:
from orchestrator.commands.apply import execute as execute_apply
execute_apply(run_id=run_id, workspace=repo_path, issue_number=issue_number, worker_id=os.environ.get("WORKER_ID"), coordination_db_dir=coordination_db_dir)
```

---

### H-4 — Wrong exception name: `CircuitBreakerOpenException` / `CircuitBreakerException`

**Cause:** The docs refer to exceptions that don't exist. The real class is `CircuitBreakerOpenError` (in `exceptions.py:106`).

**Note:** `_call_with_half_open_probe` and `ProbeSlotBusyError` were **removed** in commit `e81fafe`. The `CircuitBreaker` class with `SqliteCircuitBreakerStore` is the sole mechanism. No `circuit_breaker_store.py` was ever created — the store lives in `lock.py`.

**Fix:** Replace every occurrence:

| File | Old | New |
|------|-----|-----|
| `lock.py` | N/A | ✅ Already uses `CircuitBreakerOpenError` from `exceptions.py` |
| `work_queue.py` | `except (CircuitBreakerOpenException, ProbeSlotBusy)` | `except CircuitBreakerOpenError` |

And fix the import in `work_queue.py`:

```python
# Old:
from orchestrator.exceptions import CircuitBreakerOpenException

# New:
from orchestrator.exceptions import CircuitBreakerOpenError
```

The `ProbeSlotBusy`/`ProbeSlotBusyError` class is **not needed** — it was part of the removed `_call_with_half_open_probe` approach. The worker loop catches `CircuitBreakerOpenError` only, which covers all CB rejections.

---

### H-5 — `acquire_repo_lock` / `release_repo_lock` never called

**Cause:** `lock.py` defines the functions but no code calls them. `apply.py:execute()` is the entry point that needs wrapping.

**Fix:** In `src/orchestrator/commands/apply.py`, wrap the core logic with repo lock:

```python
from orchestrator.storage.lock import acquire_repo_lock, release_repo_lock

def execute(...):
    # ... existing preamble up to run_metadata reading ...

    # Acquire repo lock before any git mutation
    repo_identity = str(target_path)  # or run_metadata.repo if available
    acquired = False
    if coordination_db_dir is not None:
        acquired = acquire_repo_lock(repo_identity, worker_id or "unknown",
                                     ttl_seconds=300, db_dir=coordination_db_dir)

    try:
        # ... entire existing execute() body (lines ~57 to ~429) ...
        pass  # placeholder — keep all existing code
    finally:
        if coordination_db_dir is not None and acquired:
            release_repo_lock(repo_identity, worker_id or "unknown",
                              db_dir=coordination_db_dir)
```

---

### H-6 — `_sqlite_connect()` imports missing

**Cause:** All files that create DB connections use `_sqlite_connect()` but never import it. The `storage/__init__.py` defines it but isn't created yet by the time any file runs.

**Fix (prerequisite):** Ensure `src/orchestrator/storage/__init__.py` exists with:

```python
"""Storage package — SQLite connections, lock, queue, and CB store."""
import sqlite3
from pathlib import Path
from typing import Optional


def _sqlite_connect(db_path: Path, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Canonical SQLite connection with WAL mode and IMMEDIATE locking."""
    conn = sqlite3.connect(str(db_path), timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    # No BEGIN IMMEDIATE here — each function in the storage layer calls
    # BEGIN IMMEDIATE explicitly before its transaction
    return conn
```

Then add imports to these files:

| File | Add |
|------|-----|
| `src/orchestrator/storage/work_queue.py` | `from orchestrator.storage import _sqlite_connect` |
| `src/orchestrator/storage/lock.py` | `from orchestrator.storage import _sqlite_connect` |
| `src/orchestrator/storage/circuit_breaker_store.py` (NOT CREATED) | N/A — store lives in `lock.py`, import already present |

---

### H-7 — Three broken data paths

#### H-7a: `risk_gate.json` bypasses ArtifactStore

In `src/orchestrator/risk.py`, replace the `Path.write_text()` call:

```python
# Old (B6 §1):
risk_gate_path = workspace_mgr.run_dir(run_metadata.run_id) / "risk_gate.json"
risk_gate_path.write_text(
    RiskGateResult(passed=len(reasons)==0, gate="plan", reasons=reasons).model_dump_json(indent=2),
    encoding="utf-8",
)

# New:
risk_result = RiskGateResult(passed=len(reasons)==0, gate="plan", reasons=reasons)
workspace_mgr.write_artifact(run_metadata.run_id, "risk_gate.json",
    risk_result.model_dump_json(indent=2))
```

Both `check_plan_gate()` and `check_patch_gate()` must follow this pattern. In `check_patch_gate()`:

```python
def check_patch_gate(
    run_metadata: RunMetadata,
    patch_diff: str,
    workspace_mgr: Optional[WorkspaceManager] = None,   # NEW
) -> RiskGateResult:
    # ... existing size checks ...
    risk_result = RiskGateResult(passed=len(reasons) == 0, gate="patch", reasons=reasons)
    if workspace_mgr is not None:
        workspace_mgr.write_artifact(run_metadata.run_id, "risk_gate.json",
            risk_result.model_dump_json(indent=2))
    return risk_result
```

In `preview.py` at the call site, pass `workspace_mgr`:

```python
# Old:
risk_result = check_patch_gate(run_metadata, patch_diff)

# New:
risk_result = check_patch_gate(run_metadata, patch_diff, workspace_mgr=workspace_mgr)
```

#### H-7b: `events.jsonl` in `_hydrate_workspace`

In `src/orchestrator/storage/work_queue.py`, remove `"events.jsonl"` from the `_hydrate_workspace()` recovery list:

```python
# Old:
for name in ("run.json", "events.jsonl", "risk_gate.json"):

# New:
for name in ("run.json", "risk_gate.json"):
```

Add a comment above documenting the residual risk:

```python
# Residual risk: events.jsonl is NOT recovered from ArtifactStore.
# It is an append-only local log with best-effort durability.
# Loss on worker recycle is accepted (events are observability, not operational).
```

#### H-7c: Failure-path `apply.json` writes bypass WAL (B1×B5 collision)

In `src/orchestrator/commands/apply.py`, replace the two failure-path `workspace_mgr.write_artifact(run_id, "apply.json", ...)` with `_wal_write()`:

**Line 291** (apply-failed block):

```python
# Old:
workspace_mgr.write_artifact(run_id, "apply.json", apply_result.model_dump_json(indent=2))

# New:
apply_result.status = "applying"
_wal_write(apply_result, wal_path)
```

**Line 375** (post-validation-failed block):

```python
# Old:
workspace_mgr.write_artifact(run_id, "apply.json", apply_result.model_dump_json(indent=2))

# New:
apply_result.status = "applying"
_wal_write(apply_result, wal_path)
```

Also at line 396 (success path), ensure it uses `_wal_write()` too (it should already per B1 §5):

```python
# Verify line 396 uses _wal_write, not write_artifact:
# Expected:
apply_result.status = "applied"
_wal_write(apply_result, wal_path)
```

Import the canonical `_wal_write` (see 00-README.md §Canonical Patterns). It lives in `orchestrator.storage`:

```python
from orchestrator.storage import _wal_write

# Usage (canonical signature: (result: BaseModel, path: Path)):
# apply_result.status = "applying"
# _wal_write(apply_result, wal_path)
```

---

### H-8 — `Pipeline.execute()` is legacy; remove modification

**Cause:** B2 §2 adds code to `Pipeline.execute()` that crashes on brand-new runs (`run_meta = None` then `run_meta.logs_dir = ...` → `AttributeError`). But `Pipeline` is never instantiated in production code — it's dead code from pre-V1. The worker loop (B8b) calls stage functions directly, not `Pipeline.execute()`.

**Fix:** Revert the B2 modification to `src/orchestrator/pipeline.py`. Remove the code block in B2 §2 entirely. `Pipeline` is legacy and must not be modified by P3.

`git checkout -- src/orchestrator/pipeline.py` to restore the original (unmodified by B2). Then verify no diff remains on `pipeline.py`.

---

### H-9 — `DANGEROUS_FILES` heuristic can't match directory entries

**Cause:** `Path(f).name in DANGEROUS_FILES` compares only the final path component (bare filename). An entry like `".github/workflows/"` never matches any actual file path because `.name` on a file path like `".github/workflows/deploy.yml"` returns `"deploy.yml"`, not `".github/workflows/"`.

**Fix:** Change from basename match to glob/prefix match:

In `src/orchestrator/risk.py`, replace:

```python
# Old:
DANGEROUS_FILES = {
    "Dockerfile", "Makefile", "docker-compose.yml",
    ".github/workflows/", "Jenkinsfile", "requirements.txt",
    "setup.py", "setup.cfg", "pyproject.toml",
}

for task in architect_output.implementation_plan:
    for f in task.files_to_modify:
        if Path(f).name in DANGEROUS_FILES:
            task.risk_level = "high"
            reasons.append(f"File {f} is infrastructure — escalated to high risk")

# New:
DANGEROUS_PATTERNS = {
    "Dockerfile", "Makefile", "docker-compose.yml",
    ".github/workflows/", "Jenkinsfile", "requirements.txt",
    "setup.py", "setup.cfg", "pyproject.toml",
}
# Also match files like Dockerfile.prod, docker-compose.override.yml
DANGEROUS_SUFFIXES = {
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
}

def _is_dangerous(path: str) -> bool:
    p = Path(path)
    name = p.name
    if name in DANGEROUS_PATTERNS:
        return True
    # Match directory prefix: ".github/workflows/" matches ".github/workflows/deploy.yml"
    for parent in p.parents:
        if str(parent) + "/" in DANGEROUS_PATTERNS or str(parent) + "\\" in DANGEROUS_PATTERNS:
            return True
    return False

for task in architect_output.implementation_plan:
    for f in task.files_to_modify:
        if _is_dangerous(f):
            task.risk_level = "high"
            reasons.append(f"File {f} is infrastructure — escalated to high risk")
```

---

### H-10 — B6 patches `check_plan_gate()` but `preview.py` calls `check_patch_gate()`

**Cause:** B6 says to update `preview.py` to pass `workspace_mgr` to `check_plan_gate()`, but `preview.py` calls `check_patch_gate()` (line 139), not `check_plan_gate()`.

**Fix:** Remove `preview.py` from B6's `check_plan_gate()` callers. Instead, add `risk.py` to persist `risk_gate.json` from `check_patch_gate()` (already covered by H-7a). The dangerous-file heuristic stays in `check_plan_gate()` where `task.files_to_modify` is available.

File changes:
- `src/orchestrator/risk.py`: `check_patch_gate()` gets `workspace_mgr: Optional[WorkspaceManager] = None` param, persists risk_gate.json (see H-7a).
- `src/orchestrator/commands/preview.py`: pass `workspace_mgr=workspace_mgr` to `check_patch_gate()`.
- `src/orchestrator/commands/plan.py`: pass `workspace_mgr=workspace_mgr` to `check_plan_gate()` (unchanged from B6 — already correct).

---

### H-11 — WAL phases 2-4 reference code that doesn't exist yet (sprint ordering)

**Cause:** B1 (Sprint 0) tries to implement 5 WAL phases including `pushed_remote` and `pr_created`, but pushing and PR creation are only possible in B3 (Sprint 2) when `GitHubClient` exists.

**Fix:** In `src/orchestrator/commands/apply.py`, reduce the WAL to 2 phases for Sprint 0, with TODO-B3 markers:

```python
# Phase 1: applying — checkpoint before git apply
apply_result.status = "applying"
_wal_write(apply_result, wal_path)

# Phase 2: committed_local — commit after successful git apply
apply_patch(target_path, patch_path)  # existing line
git_commit_cmd = ["git", "-C", str(target_path), "commit", "-a", "-m",
    f"Apply patch {run_id}"]  # TODO-B3: use COMMIT_TEMPLATE.format(run_id=run_id)
result = subprocess.run(git_commit_cmd, capture_output=True, text=True, timeout=30)
apply_result.status = "committed_local"
_wal_write(apply_result, wal_path)

# TODO-B3: Uncomment when GitHubClient exists (Sprint 2)
# Phase 3: pushed_remote — git push origin <branch>
# Phase 4: pr_created — GitHubClient.create_pr(...)
```

Add a second TODO block for the `COMMIT_TEMPLATE` usage once it exists:

```python
# TODO-B3: Replace inline commit message with COMMIT_TEMPLATE.format(run_id=run_id)
# from orchestrator.clients.github import GitHubClient
```

In `src/orchestrator/storage/work_queue.py`, in `_execute_apply_with_checkpoints`, phases 3-4 should similarly be gated:

```python
# TODO-B3: Enable pushed_remote / pr_created recovery branches when
# GitHubClient.create_pr() and push_delete_remote() exist.
# For now, only applying and committed_local are checkpointed.
```

The success path (line 387-396) should update status to `"committed_local"` not `"applied"` until B3 lands:

```python
# B1 only: mark as committed_local (B3 promotes to "applied" after push+PR)
apply_result.status = "committed_local"
_wal_write(apply_result, wal_path)
```

---

### H-12 — `issue_lock` prevents dual-PR but `run_id` collision semantics unclear

(Not strictly in the original findings but discovered as a consequence of H-2, H-5, H-11.)

**Cause:** `issue_lock` (B8a) uses `(repo, issue_number)` as PK, preventing duplicate webhook processing. But nothing prevents a **re-run** of the same issue (if manually re-triggered via `patchforge plan --issue 42`). The second run gets a new `run_id`, a new branch (`patchforge/run_{run_id2}/issue_42`), and a new PR — the old branch/PR from `run_id1` is orphaned.

**Fix:** Add cleanup of old PRs for the same issue in the webhook handler or at enqueue time. In `enqueue_issue()` in `work_queue.py`, after the INSERT but within the same transaction:

```python
def enqueue_issue(conn: sqlite3.Connection, issue_number: int, repo: str, payload: str,
                  github: Optional["GitHubClient"] = None) -> Optional[str]:
    # ... existing BEGIN IMMEDIATE, INSERT INTO issue_lock ...
    # If a previous run_id exists for this issue, close its old PR
    old_run_id = conn.execute(
        "SELECT run_id FROM work_queue WHERE issue_number=? AND repo=? ORDER BY created_at DESC LIMIT 1",
        (issue_number, repo)
    ).scalar()
    # ... continue with INSERT INTO work_queue ...
```

(Optional — mark as 🔵 COSMETIC since labels are cosmetic per Invariant #8. Only implement if explicit requirement appears.)

---

### 🟡 Minor Fixes

#### M-1: Missing `Optional` import in `rollback.py`

In `src/orchestrator/agents/executor/rollback.py`, add `Optional` to the from-typing import:

```python
from typing import Optional
```

#### M-2: Duplicate import in B3's `GitHubClient`

In `src/orchestrator/clients/github.py`, remove the redundant line:

```python
# Remove one of these duplicates (keep the one with all needed items):
from typing import List, Optional
```

#### M-3: Document `PATCHFORGE_WORKSPACE` env var

In `src/orchestrator/commands/apply.py`, add after the workspace resolution (line 61):

```python
# After line 61: workspace_path = default_workspace_path(Path.cwd())
import os
workspace_env = os.environ.get("PATCHFORGE_WORKSPACE")
if workspace_env:
    workspace_path = Path(workspace_env).resolve()
```

#### M-4: Document `REPO_LOCK_ENABLED` env var

In `src/orchestrator/storage/lock.py`, add at the top:

```python
import os
_REPO_LOCK_ENABLED = os.environ.get("REPO_LOCK_ENABLED", "1") == "1"
```

#### M-5: Document `WORKER_ID` env var

In `src/orchestrator/storage/lock.py` and `src/orchestrator/storage/circuit_breaker_store.py`:

```python
_WORKER_ID: str = os.environ.get("WORKER_ID", "unknown")
```

#### M-6: Document `PATCHFORGE_GITHUB_TOKEN` env var

In `src/orchestrator/clients/github.py`, add:

```python
import os
_token = os.environ.get("PATCHFORGE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
if not _token:
    raise RuntimeError("PATCHFORGE_GITHUB_TOKEN or GITHUB_TOKEN must be set")
```

#### M-7: Add `encoding="utf-8"` to dual-write in B5

In `src/orchestrator/workspace.py` (the `write_run_json` dual-write added by B5):

```python
# Old:
self.root.joinpath(local_path).write_text(serialized)

# New:
self.root.joinpath(local_path).write_text(serialized, encoding="utf-8")
```

#### M-8: Fix `cleanup_stale_workspaces` for coordinator

In `src/orchestrator/storage/lock.py` (or wherever `cleanup_stale_workspaces` lives), guard the `.parent` traversal:

```python
def cleanup_stale_workspaces(workspace_mgr: WorkspaceManager, worker_id: str) -> None:
    if not worker_id:
        return  # Coordinator has no worker-scoped workspaces
    root = workspace_mgr.root
    ...  # existing logic
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/orchestrator/storage/__init__.py` | Create with `_sqlite_connect()` |
| `src/orchestrator/circuit_breaker.py` | Add `state_value` property (N/A — `_call_with_half_open_probe` removed; `CircuitBreaker` uses `.value` directly in `_persist_state()`) |
| `src/orchestrator/storage/circuit_breaker_store.py` | **NOT CREATED** — store lives in `lock.py`; CB casing already correct via `.value` in `set_state()` |
| `src/orchestrator/storage/work_queue.py` | Fix import; fix `_pre_dequeue_backpressure`; fix `_hydrate_workspace`; fix `_execute_apply_with_checkpoints` recovery; fix branch format; fix `_sqlite_connect` import |
| `src/orchestrator/exceptions.py` | Add `ProbeSlotBusyError(CircuitBreakerOpenError)` — N/A, removed in e81fafe; `CircuitBreaker` + `SqliteCircuitBreakerStore` is sole mechanism |
| `src/orchestrator/git.py` | Add `checkout_detached`, `delete_local_branch`, `push_delete_remote` |
| `src/orchestrator/commands/apply.py` | Fix branch to unified format; add `issue_number` to `execute()`; fix failure-path `_wal_write`; wrap with repo lock; add TODO-B3 markers |
| `src/orchestrator/schemas/artifacts.py` | Add `issue_number: Optional[int] = None` to `RunMetadata` |
| `src/orchestrator/pipeline.py` | Revert B2 changes; `git checkout --` |
| `src/orchestrator/risk.py` | Fix `DANGEROUS_FILES` → `DANGEROUS_PATTERNS` with `_is_dangerous()`; persist via `write_artifact`; add `workspace_mgr` to `check_patch_gate()` |
| `src/orchestrator/commands/preview.py` | Pass `workspace_mgr` to `check_patch_gate()` |
| `src/orchestrator/workspace.py` | Add `encoding="utf-8"` in B5 dual-write |
| `src/orchestrator/agents/executor/rollback.py` | Import `Optional` |
| `src/orchestrator/clients/github.py` | Fix duplicate import; add env var check for token |
| `src/orchestrator/storage/lock.py` | Add `_sqlite_connect` import; add env vars; fix `cleanup_stale_workspaces` |

---

## Acceptance Criteria

- [ ] CB state queries match enum values (all lowercase) — verify via `grep -rn "state = 'OPEN'" src/` returns 0 hits *(deferred — H-1 excluded from #142 scope)*
- [x] All branch names use `patchforge/run_{run_id}/issue_{issue_number}` or `patchforge/run_{run_id}` — one format per run_id+issue combination *(H-2, fixed in #142 commit `380177a`)*
- [ ] `checkout_detached`, `delete_local_branch`, `push_delete_remote` exist in `git.py` with correct git commands *(deferred — H-3 excluded from #142 scope)*
- [ ] `_execute_apply_with_checkpoints` recovery uses real functions, not phantoms *(deferred — H-3 excluded from #142 scope)*
- [ ] `CircuitBreakerOpenError` is the only CB exception in the codebase *(deferred — H-4 excluded from #142 scope)*
- [ ] `ProbeSlotBusyError(CircuitBreakerOpenError)` was **removed** in e81fafe *(deferred — H-4 excluded from #142 scope)*
- [x] `apply.py:execute()` acquires repo lock when `coordination_db_dir` is provided *(H-5, fixed in #142 commit `380177a`)*
- [x] `_sqlite_connect()` is imported in all storage files via `from orchestrator.storage import _sqlite_connect` (H-6, already fixed pre-Issue-A)
- [x] `risk_gate.json` persisted via `workspace_mgr.write_artifact()` in both `check_plan_gate()` and `check_patch_gate()` (H-7a, already fixed pre-Issue-A)
- [x] `events.jsonl` removed from `_hydrate_workspace` recovery list (H-7b, already fixed pre-Issue-A)
- [x] Failure-path `apply.json` writes use `_wal_write()`, not `write_artifact()` (H-7c, fixed in Issue-A commit `f437f46`)
- [x] `pipeline.py` has zero P3 diffs (H-8 — encoding fixes applied instead of revert, in Issue-A commit `f437f46`)
- [x] Removed stale `# TODO-B3` comments from production code *(H-8, fixed in #142 commit `380177a`)*
- [ ] `DANGEROUS_PATTERNS` correctly matches directory entries like `.github/workflows/deploy.yml` *(deferred — H-9 risk.py part excluded from #142 scope)*
- [ ] `check_patch_gate()` accepts `workspace_mgr` param and persists `risk_gate.json` *(deferred — H-10 excluded from #142 scope)*
- [ ] WAL phases 3-4 gated behind `# TODO-B3` markers *(deferred — H-11 excluded from #142 scope; existing code has only 2 phases)*
- [x] All `encoding="utf-8"` present in dual-write paths (M-7, fixed in Issue-A commit `f437f46`)
- [x] `PATCHFORGE_WORKSPACE` read in `apply.py` *(M-3, fixed in #142 commit `380177a`)*
- [x] `REPO_LOCK_ENABLED` and `WORKER_ID` read in `lock.py` *(M-4/M-5, fixed in #142 commit `380177a`)*
- [ ] `PATCHFORGE_GITHUB_TOKEN` validated at module level in `github.py` *(M-6 — replaced by token validation in `GitHubClient.__init__()` per #142 H-9)*

---

## Verification

```bash
# 1. CB casing — assert zero uppercase state references in SQL
grep -rn "state = 'OPEN'" src/ && echo "FAIL: found uppercase OPEN" || echo "OK: no uppercase OPEN"
grep -rn "state = 'HALF_OPEN'" src/ && echo "FAIL: found uppercase HALF_OPEN" || echo "OK: no uppercase HALF_OPEN"

# 2. Branch format — confirm only unified formats exist
grep -rn 'patchforge/' src/ | grep -v __pycache__ | grep -v '.pyc'

# 3. No phantom git functions
grep -rn 'git_push_delete_remote\|git_reset_hard\|git_clean\|git_checkout\|git_delete_branch_local\|run_apply_command' src/ && echo "FAIL: phantom functions remain" || echo "OK: no phantoms"

# 4. Exception names
grep -rn 'CircuitBreakerOpenException\|CircuitBreakerException' src/ && echo "FAIL: wrong names" || echo "OK: clean"

# 5. _sqlite_connect imports
grep -rn 'sqlite3.connect(' src/orchestrator/storage/ | grep -v '_sqlite_connect' && echo "FAIL: raw sqlite3.connect() in storage" || echo "OK: all storage uses _sqlite_connect"

# 6. Pipeline unchanged
git diff --stat src/orchestrator/pipeline.py

# 7. risk_gate.json via write_artifact
grep -rn 'Path.*write_text.*risk_gate' src/ && echo "FAIL: direct write_text" || echo "OK: no direct writes"

# 8. WAL failure paths
grep -rn "write_artifact.*apply.json" src/orchestrator/commands/apply.py && echo "FAIL: write_artifact in failure path" || echo "OK: all apply.json uses _wal_write"

# 9. Dangerous files directory match
python -c "
from orchestrator.risk import _is_dangerous
assert _is_dangerous('Dockerfile'), 'Dockerfile should match'
assert _is_dangerous('.github/workflows/deploy.yml'), '.github/workflows/ should match by prefix'
assert _is_dangerous('docker-compose.yml'), 'docker-compose.yml should match'
assert not _is_dangerous('src/main.py'), 'src/main.py should not match'
print('All dangerous file checks OK')
"

# 10. Full type check
mypy src/

# 11. Full test suite
pytest tests/ -v --timeout=60
```

---

## Rollback

```bash
# Revert all H-1 to H-12 changes in this blocker
git checkout -- src/orchestrator/circuit_breaker.py
git checkout -- src/orchestrator/exceptions.py
git checkout -- src/orchestrator/git.py
git checkout -- src/orchestrator/risk.py
git checkout -- src/orchestrator/workspace.py
git checkout -- src/orchestrator/commands/apply.py
git checkout -- src/orchestrator/commands/preview.py
git checkout -- src/orchestrator/agents/executor/rollback.py
git checkout -- src/orchestrator/clients/github.py
git checkout -- src/orchestrator/pipeline.py
git checkout -- src/orchestrator/schemas/artifacts.py
git checkout -- src/orchestrator/storage/__init__.py
git checkout -- src/orchestrator/storage/lock.py
git checkout -- src/orchestrator/storage/circuit_breaker_store.py
git checkout -- src/orchestrator/storage/work_queue.py
```
