# P3 Migration — Prompt Files Index

**Status:** Generated from `docs/planning/p3-migration-plan.md`
**Target:** Make the pipeline worker-safe with async workers & CI/CD integration

---

## Status

> Update this table after every blocker: `git rev-parse --short HEAD` → paste commit, flip status.

| Sprint | Blocker | Status | Branch | Commit | Notes |
|--------|---------|--------|--------|--------|-------|
| 0 | B6 — Risk Gate Audit Trail | ✅ Done | `feat/issue-118-risk-gate-audit-trail` | `b2b769d` | `_is_dangerous()`, `risk_gate.json` artifact, `failure_artifacts` |
| 0 | B1 — WAL Atomic Apply | ✅ Done | `feat/issue-121-b1-wal-atomic-apply` | `ccba78e` | WAL atomic apply with crash-safe 5-phase checkpointing via `_wal_write` |
| 0 | B2 — RunMetadata SSoT | ❌ Pending | — | — | |
| 1 | B4 — CB Externalized (SQLite) | ❌ Pending | — | — | |
| 1 | B7 — Workspace Isolation | ❌ Pending | — | — | |
| 2 | B8a — Work Queue Schema | ❌ Pending | — | — | |
| 2 | B8b — Worker Loop | ❌ Pending | — | — | |
| 2 | B3 — GitHub Client | ❌ Pending | — | — | |
| 2 | B5 — Artifact Store | ❌ Pending | — | — | |
| — | Post-Audit Fixes | ❌ Pending | — | — | |

---

## Execution Order

Execute files **within each sprint in order**. Sprint boundaries are strict (Sprint 0 → Sprint 1 → Sprint 2).

```
Sprint 0 (Foundation)
├── 01-b1-wal.md           WAL Atomic Apply
├── 02-b2-runmetadata.md   RunMetadata Single Source of Truth
└── 03-b6-risk-gate.md     Risk Gate Audit Trail

Sprint 1 (Distribution Primitives)
├── 01-b4-circuit-breaker.md   Externalized CB State (SQLite)
└── 02-b7-workspace-isolation.md  Workspace Isolation + Repo Lock

Sprint 2 (CI/CD Surface)
├── 01-b8a-work-queue-schema.md  Work Queue Schema & Enqueue/Dequeue
├── 01-b8b-worker-loop.md        State-Machine Worker Loop + Resume
├── 02-b3-github.md            GitHub Client + Webhook
└── 03-b5-artifact-store.md    Pluggable ArtifactStore
```

---

## Repository Layout (source directories referenced)

| Path | Purpose |
|------|---------|
| `src/orchestrator/commands/` | CLI entry points (`apply.py`, `plan.py`, `preview.py`) |
| `src/orchestrator/agents/executor/` | Execution agents, providers, rollback |
| `src/orchestrator/schemas/` | Pydantic models (`artifacts.py`, `risk.py`, `pipeline_run.py`) |
| `src/orchestrator/clients/` | LLM clients (Gemini, Groq, Anthropic) + `bootstrap.py` |
| `src/orchestrator/` | Core: `workspace.py`, `pipeline.py`, `circuit_breaker.py`, `risk.py`, `git.py` |

---

## Architecture Invariants (must never be violated)

1. **Single source of truth:** `run.json` (RunMetadata) is the only context schema. No parallel `WorkerContext` or `PipelineRun` for routing.
2. **WAL bypasses ArtifactStore:** `apply.json` with `status: "applying"` writes directly to local filesystem — never delegated to a pluggable store.
3. **Circuit Breaker OPEN = zero calls:** No LLM call executes when state is OPEN. Guarda is inside `_call_with_half_open_probe`, not just `_pre_dequeue_backpressure`.
4. **run_id ↔ patch bijection:** Each `run_id` produces exactly one patch. Checkpoints guarantee LLM stages run at most once per `run_id`.
5. **Queue = source of truth for work:** Webhook enqueues via single-DB ACID transaction. `issue_lock` prevents duplicate admission.
6. **Branch name is immutable idempotency key:** `patchforge/run_{run_id}/issue_{issue_number}` — never read PR body or GitHub labels for idempotency.
7. **Two SQLite stores for blast-radius isolation:** `coordination.db` (CB + locks) and `queue.db` (queue + checkpoints). Corruption in one never blocks the other.
8. **Labels are cosmetic:** GitHub labels updated asynchronously after SQLite commit. Label failure never blocks pipeline execution.

---

## Base Commands

```bash
# Run tests
pytest tests/ -v

# Run specific test file
pytest tests/test_circuit_breaker.py -v

# Lint
ruff check src/

# Type check
mypy src/
```

---

## Canonical Patterns

To eliminate duplicated bug classes, use these helpers across all blockers.

### `_wal_write` (Atomic write)
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

### `_sqlite_connect` (Safe DB Connection)

Lives in `src/orchestrator/storage/__init__.py`. Import across modules:
```python
from orchestrator.storage import _sqlite_connect
```

```python
import sqlite3
from pathlib import Path

def _sqlite_connect(db_path: Path, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Canonical SQLite connection with WAL mode and IMMEDIATE locking."""
    conn = sqlite3.connect(str(db_path), timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn
```
*Never call `sqlite3.connect()` directly — always use `_sqlite_connect()`.*

### `_with_retry` (GitHub API Resilience)
```python
import random
import time
from github import GithubException

def _with_retry(self, fn, *args, max_retries: int = 3, **kwargs):
    """Retry wrapper for GitHub API calls with jitter on rate limit."""
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except GithubException as e:
            if e.status == 403 and "rate limit" in str(e).lower():
                wait = int(e.headers.get("Retry-After", 60))
                time.sleep(wait + random.uniform(0, 5))
                continue
            raise
    raise RuntimeError(f"Max retries ({max_retries}) exceeded on rate limit")
```

---

## Session Starter

Paste at the beginning of each Claude Code Pro session:

```text
You are implementing the P3 migration of PatchForge.
Repo layout: src/orchestrator/{commands,agents,schemas,storage,clients,integrations}
Active invariants (never violate):
1. run.json is the only context schema — no WorkerContext
2. apply.json WAL writes directly to filesystem — never to ArtifactStore
3. CB OPEN = zero calls — guard is inside _call_with_half_open_probe
4. Branch name is the idempotency key — never PR body or labels
5. Two SQLite stores: coordination.db and queue.db
6. _wal_write REQUIRES fsync — tmp.stat() does NOT guarantee durability
7. isolation_level=None everywhere — explicit transactions always
8. _sqlite_connect() is the ONLY sqlite3.connect() in the codebase
This session implements: [PASTE BLOCKER NAME]
Only modify files listed in "Files to Modify/Create".
Run after each change: pytest tests/ -v && ruff check src/
```

---

## Session close format

After implementing a blocker, paste this into `HANDOFF.md` to update the status table:

```
Final output:
| Blocker | Status | Branch | Commit | Summary |
|---------|--------|--------|--------|---------|
| B1 — WAL Atomic Apply | ✅ Done | `feat/issue-XXX` | `COMMIT` | [1-line summary] |
Tests: X passed, 0 failed
TODOs: [none / list]
```

---

## Post-implementation checklist (run after each blocker)

- [ ] `pytest tests/ -v` → all existing tests still pass
- [ ] `ruff check src/` → 0 errors
- [ ] `mypy src/` → 0 new errors
- [ ] `git diff --stat` → only files listed in "Files to Modify" were touched
- [ ] No new `sqlite3.connect()` calls without `row_factory`
- [ ] No new status mutations without `_wal_write()` call

---

## Per-File Structure

Every prompt file follows this template:

1. **Goal** — 1-paragraph objective
2. **Current state** — Real code snippets showing what exists today (file:line)
3. **Changes** — Exact code to insert, with file paths and target locations
4. **Files to modify/create** — List
5. **Acceptance Criteria** — Checklist
6. **Verification** — Commands to run for each AC
7. **Rollback** — How to revert if something goes wrong
