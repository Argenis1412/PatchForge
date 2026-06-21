# B1 — Write-Ahead Log (WAL) for Atomic Apply

## Goal

Make `git apply` crash-safe. If a worker dies between a successful `git apply` and the `apply.json` write, the repo must be left in a detectable mixed state so a recovery worker can roll back cleanly.

---

## Current State

### `src/orchestrator/schemas/artifacts.py:91-100` — ApplyResult (no `status` field)

```python
class ApplyResult(BaseModel):
    run_id: str
    applied_at: datetime
    branch: str
    success: bool
    rolled_back: bool = False
    error: Optional[str] = None
    pre_apply_head: Optional[str] = None
    pre_apply_branch: Optional[str] = None
    rollback_head: Optional[str] = None
```

No `status` field to track intermediate phases (`applying`, `committed_local`, etc.). The only persistence is after all git operations complete.

### `src/orchestrator/commands/apply.py:214-219` — Saves single checkpoint before apply

```python
# 6. Save pre-apply Git state
pre_apply_head = current_head(target_path)
pre_apply_branch = current_branch(target_path)
```

Only one pre-apply snapshot. No intermediate checkpoints during the apply phases.

### `src/orchestrator/commands/apply.py:244-291` — `git apply` in one shot

```python
# 8. Apply patch
apply_res = apply_patch(target_path, patch_path)
if apply_res.return_code != 0:
    # Revert: force reset to pre-apply state
    rollback_succeeded = False
    try:
        rollback_to_commit(target_path, pre_apply_head)
        rollback_succeeded = True
    except RollbackError as exc:
        console.print(
            "[bold red]FATAL: Patch application failed AND the automatic revert also failed. "
            "Your repository may be in a partially applied state.\n"
            f"Revert stderr: {exc.stderr}\n"
            "Please run 'git checkout .' and 'git clean -fd' manually "
            "to restore a clean state.[/bold red]"
        )
    apply_result = ApplyResult(
        run_id=run_id,
        applied_at=datetime.now(timezone.utc),
        branch=branch_name,
        success=False,
        rolled_back=rollback_succeeded,
        error=apply_res.stderr,
        pre_apply_head=pre_apply_head,
        pre_apply_branch=pre_apply_branch,
        rollback_head=pre_apply_head if rollback_succeeded else None,
    )
    workspace_mgr.write_artifact(run_id, "apply.json", apply_result.model_dump_json(indent=2))
```

Patch is applied in one `git apply` call. If worker dies after `git apply` succeeds but before `write_artifact("apply.json")`, the repo is in an unrecoverable mixed state.

### `src/orchestrator/agents/executor/rollback.py:8-18` — Requires target SHA only

```python
def rollback_to_commit(repo_root: Path, target_sha: str) -> None:
    from orchestrator.exceptions import RollbackError
    from orchestrator.git import force_reset_apply

    result = force_reset_apply(repo_root, target_sha)
    if result.return_code != 0:
        raise RollbackError(
            repo_root=repo_root,
            target_sha=target_sha,
            stderr=result.stderr,
        )
```

No backup diff is preserved. Rollback relies solely on `force_reset_apply` (hard reset), losing any uncommitted changes.

---

## Changes

### 1. Add `status` and `pre_apply_diff_backup` fields to `ApplyResult`

`src/orchestrator/schemas/artifacts.py`:

```python
class ApplyResult(BaseModel):
    run_id: str
    applied_at: datetime
    branch: str
    success: bool
    status: str = "pending"        # NEW: applying | committed_local | pushed_remote | pr_created | applied
    rolled_back: bool = False
    error: Optional[str] = None
    pre_apply_head: Optional[str] = None
    pre_apply_branch: Optional[str] = None
    rollback_head: Optional[str] = None
    pre_apply_diff_backup: Optional[str] = None  # NEW: path to the backup diff
    pr_number: Optional[int] = None              # NEW: for pr_created phase
```

### 2. Add `import os` and Write WAL initial state BEFORE git operations

`src/orchestrator/commands/apply.py`:
- Add `import os` to the existing imports at top of file.
- Define `_wal_write()` helper once (see 00-README.md §Canonical Patterns).
- Insert WAL initialization **after** `branch_name = f"patchforge/{run_id}"` (L219, to be updated to unified format post-B2) and **before** `create_controlled_branch` (L220):

```python
# WAL: Write apply.json with status "applying" BEFORE any git operation.
# This writes directly to local filesystem (never delegates to ArtifactStore).
apply_result = ApplyResult(
    run_id=run_id, applied_at=datetime.now(timezone.utc),
    branch=branch_name,  # placeholder — updated after branch creation
    success=False, status="applying",
    pre_apply_head=pre_apply_head, pre_apply_branch=pre_apply_branch,
)
_wal_write(apply_result, run_dir / "apply.json")
```

### 3. Backup `patch.diff` before applying

```python
import shutil
backup_path = run_dir / "patch.apply-backup.diff"
shutil.copy2(patch_path, backup_path)
```

### 4. Update rollback to accept optional backup diff

`src/orchestrator/agents/executor/rollback.py`:

```python
def rollback_to_commit(
    repo_root: Path,
    target_sha: str,
    backup_diff: Optional[Path] = None,  # NEW
) -> None:
    from orchestrator.exceptions import RollbackError
    from orchestrator.git import force_reset_apply

    if backup_diff and backup_diff.exists():
        # Attempt git apply --reverse first (preserves working tree)
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(repo_root), "apply", "--reverse", str(backup_diff)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return  # reverse apply succeeded — clean state

    result = force_reset_apply(repo_root, target_sha)
    if result.return_code != 0:
        raise RollbackError(
            repo_root=repo_root,
            target_sha=target_sha,
            stderr=result.stderr,
        )
```

### 5. Extend apply with phased checkpointing (Sprint 0: first 2 phases only)

Each crash point writes a new checkpoint status. On worker recovery, the WAL guides the exact rollback action.

| Crash point | WAL status | Rollback (persistent) | Rollback (Docker ephemeral) |
|-------------|------------|-----------------------|----------------------------|
| Before `git apply` | `applying` | `git reset --hard pre_apply_head` | `git reset --hard pre_apply_head` |
| After `git commit`, before `git push` | `committed_local` | `git reset --hard pre_apply_head` + `git branch -D branch` | **No-op** (commit lost with container) |
| After `git push`, before `create_pr` *(B3)* | `pushed_remote` | `git reset --hard pre_apply_head` + `git branch -D branch` + `git push origin --delete branch` | `git push origin --delete branch` |
| After `create_pr` *(B3)* | `pr_created` | `gh pr close` + `git reset --hard pre_apply_head` + `git branch -D branch` + `git push origin --delete branch` | `gh pr close` + `git push origin --delete branch` |

> **Sprint ordering note:** V1 `apply.py` only applies the patch locally and tells the user to `git commit` manually. It has no push or PR creation logic. Phases 3-4 require `GitHubClient` (B3, Sprint 2). This blocker implements phases 1-2 and leaves TODO-B3 markers.

Update `apply.py` to checkpoint after each phase:

```python
# Phase 1: about to commit
apply_result.status = "applying"
_wal_write(apply_result, run_dir / "apply.json")
# ... existing git apply logic ...

# Phase 2: committed locally
# V1 does not auto-commit — we add a git commit here
import subprocess
commit_msg = f"Apply patch {run_id}"
subprocess.run(["git", "-C", str(target_path), "commit", "-a", "-m", commit_msg],
    capture_output=True, text=True, timeout=30)
apply_result.status = "committed_local"
_wal_write(apply_result, run_dir / "apply.json")

# TODO-B3: Uncomment phases 3-4 when GitHubClient exists (Sprint 2, docs/planning/p3/sprint-2/02-b3-github.md).
# Phase 3: pushed, about to create PR
# apply_result.status = "pushed_remote"
# _wal_write(apply_result, run_dir / "apply.json")
# # ... existing push logic ...

# Phase 4: PR created
# apply_result.status = "pr_created"
# _wal_write(apply_result, run_dir / "apply.json")
# # ... existing finalization ...

# Phase 5: complete (Sprint 0: "committed_local"; B3 promotes to "applied")
apply_result.status = "committed_local"
apply_result.success = True
_wal_write(apply_result, run_dir / "apply.json")
```

---

## Files to Modify

- `src/orchestrator/schemas/artifacts.py` — Add fields to `ApplyResult`
- `src/orchestrator/commands/apply.py` — WAL write before git ops, backup patch, checkpoint phases 1-2, TODO-B3 for 3-4
- `src/orchestrator/agents/executor/rollback.py` — Accept optional backup diff, attempt reverse apply first

---

## Acceptance Criteria

- [ ] Worker crash mid-apply leaves `apply.json` with `status: "applying"` — detectable by recovery worker
- [ ] `patch.apply-backup.diff` exists in run directory for manual recovery
- [ ] Rollback works from backup diff via `git apply --reverse` even if `force_reset_apply` fails
- [ ] `git log patchforge/run_{run_id} ^main` returns exactly 1 commit — the PatchForge commit (once B3 enables push)
- [ ] Each checkpoint phase writes atomically (tmp + replace)
- [ ] TODO-B3 markers present for phases 3-4 — B3 (Sprint 2) uncomments and wires `GitHubClient`

---

## Test skeleton (create before running pytest)

Create `tests/test_apply_wal.py` with these cases:
```python
def test_wal_written_before_git_apply():
    """Mock git.apply, kill after WAL write, assert apply.json has status='applying'."""
    pass

def test_atomic_write_on_crash():
    """Simulate os.replace failure, assert no partial file."""
    pass

def test_each_phase_persists_to_disk():
    """Status transition committed_local writes atomically to disk."""
    pass
```

## Verification

```bash
# Test existing apply behavior still works
pytest tests/ -k test_apply -v

# Verify WAL write is atomic
pytest tests/test_apply_wal.py -v
```

## Rollback

```bash
git checkout -- src/orchestrator/schemas/artifacts.py
git checkout -- src/orchestrator/commands/apply.py
git checkout -- src/orchestrator/agents/executor/rollback.py
```
