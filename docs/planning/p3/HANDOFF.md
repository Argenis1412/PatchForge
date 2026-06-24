# PatchForge P3 — Handoff Document  
**Status:** Plan audited and hardened. Ready for implementation.  
**Audit rounds completed:** 6  
**Open BLOCKINGs:** 0 (all resolved in prompt files or in 04-post-audit-fixes.md)

---

## The Project

**PatchForge** is a code patch automation tool that analyzes GitHub issues, generates patches with LLMs, validates them with tests, and opens reviewable PRs. P3 is the migration from local CLI to distributed workers with GitHub integration.

**Stack:** Python, SQLite (WAL mode), PyGithub, Pydantic v2, Ruff, Pytest.

**Repo layout:**
```
src/orchestrator/
  commands/        # CLI commands (plan, preview, apply, rollback)
  agents/          # LLM agents (architect, scout, executor)
  schemas/         # Pydantic models (artifacts.py, architect_output.py, etc.)
  storage/         # __init__.py (canonical helpers), lock.py, work_queue.py (NEW P3)
  clients/         # github.py (NEW P3)
  integrations/    # webhook.py (NEW P3)
```

---

## P3 Plan Structure

Prompt files are in `docs/planning/p3/`:

```
00-README.md               ← Session Starter + Canonical Patterns + blocker order
sprint-0/
  01-b1-wal.md             ← Write-Ahead Log for apply.py (atomic 5-phase checkpoint)
  02-b2-runmetadata.md     ← RunMetadata as source of truth (Pipeline.execute legacy)
  03-b6-risk-gate.md       ← Risk gate audit trail + DANGEROUS_FILES heuristic
sprint-1/
  01-b4-circuit-breaker.md ← Circuit Breaker externalized to SQLite
  02-b7-workspace-isolation.md ← Workspace isolation + repo lock (acquire/release)
sprint-2/
  01-b8a-work-queue-schema.md  ← SQLite work queue schema + enqueue_issue
  01-b8b-worker-loop.md        ← Worker loop state machine + pipeline resume
  02-b3-github.md              ← GitHubClient + webhook handler
  03-b5-artifact-store.md      ← ArtifactStore abstraction + LocalArtifactStore
  04-post-audit-fixes.md       ← Patches to apply AFTER implementing B1-B8b
```

---

## Canonical Patterns (from 00-README.md)

Three helpers defined once in `src/orchestrator/storage/__init__.py`:

### _wal_write(result: BaseModel, path: Path) → None
Atomic WAL write with guaranteed fsync. Call after EVERY status change.
```python
import os
from pathlib import Path
from pydantic import BaseModel

def _wal_write(result: BaseModel, path: Path) -> None:
    """Atomic WAL write with guaranteed OS fsync. Call after EVERY status change."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))
        f.flush()                  # flush Python buffer → OS buffer cache
        os.fsync(f.fileno())       # force OS buffer cache → physical disk
    os.replace(tmp, path)          # atomic rename (POSIX) / near-atomic (Windows)
    if os.name == "posix":
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)       # persist directory entry for crash-safe rename
        finally:
            os.close(dir_fd)
```

### _sqlite_connect(db_path: Path, *, timeout: float = 30.0) → sqlite3.Connection
```python
def _sqlite_connect(db_path: Path, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Canonical SQLite connection with WAL mode and IMMEDIATE locking."""
    conn = sqlite3.connect(str(db_path), timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn
```
**NEVER call sqlite3.connect() directly. Always use _sqlite_connect().**

### _with_retry(self, fn, *args, max_retries=3, **kwargs)
GitHubClient method for retry with jitter on rate limit. See 02-b3-github.md.

---

## P3 Invariants (never violate)

1. `run.json` is the only context schema — no WorkerContext exists
2. `apply.json` WAL writes directly to filesystem — never to ArtifactStore
3. CB OPEN = zero calls — `CircuitBreaker.call()` reads state from shared SQLite via `_reload_state()` and rejects before any LLM call
4. `run_id` ↔ patch bijection — each `run_id` produces exactly one patch
5. Queue = source of truth for work — `issue_lock` prevents duplicate admission
6. Branch name is immutable idempotency key — `patchforge/run_{run_id}/issue_{issue_number}`
7. Two SQLite stores for blast-radius isolation: `coordination.db` and `queue.db`
8. Labels are cosmetic — label failure never blocks pipeline execution

---

## Implementation Order

**B1, B2, B4 completed.** Next: B7 → B8a → B8b → B3 → B5.
**B8a must be complete before opening B8b.**
**After B8b is complete:** apply `04-post-audit-fixes.md`.

---

## Known Bugs Resolved (audit history)

### Rounds 1-2 (audited and corrected in blocker files)
- Missing `import os` in apply.py
- Phase checkpoints not persisted to disk (each phase needs _wal_write)
- `pipeline_checkpoint` defined in B7 and B8 (moved only to B8)
- `row_factory = sqlite3.Row` missing in B4 and B7 (resolved via _sqlite_connect)
- `_rate_limit_retry()` context manager broken in B3 (replaced by _with_retry)
- PyGithub missing from B3 dependencies
- `DurabilityLevel(str, Enum)` for clean serialization with Pydantic v2
- Non-existent test files in Verification (skeletons added to each blocker)

### Round 3 (audited and corrected)
- CB stayed OPEN forever (missing time-window check + HALF_OPEN transition)
- Lock leak in enqueue_issue from OperationalError (resolved with explicit BEGIN IMMEDIATE)
- `tmp.stat()` does not guarantee fsync → replaced by f.flush() + os.fsync()
- `read_artifact` passed absolute path to remote store → changed to canonical ref `f"{run_id}/{name}"`
- B2/B5 gap: explicit TODO-B5 comment + B5 closes the gap in "Changes" section
- `DANGEROUS_FILES` substring match → `Path(f).name` for exact match

### Rounds 4-5 (concurrency subtleties)
- SQLite timeout=30.0 added to `_sqlite_connect`
- `isolation_level=None` for autocommit + explicit BEGIN IMMEDIATE everywhere
- `release_repo_lock()` defined in B7 + called in `finally:` of pipeline
- `BranchRef -> Optional[Any]` (PyGithub does not expose BranchRef)
- Worker loop: outer try-catch with stderr.write + sleep(10) to avoid crash loops
- `_sqlite_connect` with explicit home in `storage/__init__.py`

### Round 6 (deep bugs — combination of blocker files + 04-post-audit-fixes.md)
- H-1: CB casing mismatch ("OPEN" vs CircuitBreakerState.OPEN.value) → fixed in B4
- H-4: CircuitBreakerOpenException → CircuitBreakerOpenError → fixed in B4
- H-8: Pipeline.execute() is dead code — B2 reverted, worker calls stage functions directly
- H-11: WAL phases 3-4 require B3 (GitHubClient) — TODO-B3 markers in B1
- H-3, H-5, H-7, H-9, H-10 → in 04-post-audit-fixes.md

---

## Bugs in the Fix File (04-post-audit-fixes.md) — Pending Correction

These 3 bugs are INSIDE the post-audit file. Fix before applying it:

### BUG-B (🔴): H-5 calls release_repo_lock with wrong signature
`acquire_repo_lock` returns `bool`, not a connection. Correct signature:
`release_repo_lock(repo_identity, worker_id, db_dir=coordination_db_dir)`

### BUG-C (🔴): H-7c defines local _wal_write without fsync + collides with canonical
Remove the local definition in apply.py. Import from storage:
`from orchestrator.storage import _wal_write`
And use the canonical signature: `_wal_write(apply_result, wal_path)`.

### BUG-D (🟡): H-2 uses "row" in dir() to check local variable — always False
Pass `issue_number` as explicit parameter to `_execute_pipeline_with_resume()`.

---

## Unresolved Issues in Blocker Files

### F-1 (🔴): LocalArtifactStore.write() uses tmp.stat() without fsync (in B5)
```python
# Replace:
tmp.write_text(data, encoding="utf-8")
tmp.stat()  # ← does NOT guarantee durability

# With:
if isinstance(data, str):
    with tmp.open("w", encoding="utf-8") as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
else:
    with tmp.open("wb") as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
os.replace(tmp, full_path)
```

### F-2 (🔴): LocalArtifactStore.read() does not resolve canonical ref against self._base (in B5)
```python
# Replace:
return Path(ref).read_text(encoding="utf-8")  # reads from CWD

# With:
path = Path(ref)
if path.is_absolute():
    return path.read_text(encoding="utf-8")
return (self._base / ref).read_text(encoding="utf-8")
```

---

## Session Starter (paste at the beginning of each Claude Code session)

```
You are implementing the P3 migration of PatchForge.
Repo: src/orchestrator/{commands,agents,schemas,storage,clients,integrations}

Active invariants (NEVER violate):
1. run.json is the only context schema — no WorkerContext
2. apply.json WAL writes directly to filesystem — never to ArtifactStore
3. CB OPEN = zero calls — guard is inside CircuitBreaker.call() with _reload_state() from shared SqliteCircuitBreakerStore
4. run_id ↔ patch bijection — each run_id produces exactly one patch
5. Queue = source of truth for work — issue_lock prevents duplicate admission
6. Branch name is immutable idempotency key — patchforge/run_{run_id}/issue_{issue_number}
7. Two SQLite stores for blast-radius isolation: coordination.db and queue.db
8. Labels are cosmetic — label failure never blocks pipeline execution

This session implements: [PASTE BLOCKER NAME]
Only modify files listed in "Files to Modify/Create".
Run after each change: pytest tests/ -v && ruff check src/
```

---

## Post-Implementation Checklist (run after each blocker)

```bash
pytest tests/ -v              # all existing tests still pass
ruff check src/               # 0 errors
mypy src/                     # 0 new errors
git diff --stat               # only files in blocker scope
grep -rn "sqlite3\.connect(" src/ --include="*.py"  # must give 0 results (use _sqlite_connect)
grep -rn "tmp\.stat()" src/ --include="*.py"        # must give 0 results (use fsync)
```

---

## Session Close Format (paste after each blocker)

```
Final output:
| Blocker | Status | Branch | Commit | Summary |
|---------|--------|--------|--------|---------|
| B1 — WAL Atomic Apply | ✅ Done | `feat/issue-XXX` | `COMMIT` | [1-line summary] |
| Tests | X passed, 0 failed | — | — | — |
| TODOs | [none / list] | — | — | — |
```

---

## Last Completed Blocker

| Blocker | Status | Branch | Commit | Summary |
|---------|--------|--------|--------|---------|
| B1 — WAL Atomic Apply | ✅ Done | `feat/issue-121-b1-wal-atomic-apply` | `ccba78e` | WAL atomic apply with crash-safe 5-phase checkpointing via `_wal_write` |
| B2 — RunMetadata SSoT | ✅ Done | `feat/issue-123-runmetadata-ssot` | `c59274b` | 9 execution-context fields added to RunMetadata; WorkspaceManager env-var fallback |
| B4 — CB Externalized (SQLite) | ✅ Done | `feat/issue-126-cb-externalized` | `ac978c7` | SQLite-backed CB with `_reload_state()`, `time.time()`, `SqliteCircuitBreakerStore`; `_call_with_half_open_probe` removed; cross-worker state sharing + exponential backoff |
| Tests | 417 passed, 0 failed | — | — | — |
| TODOs | none | — | — | — |
