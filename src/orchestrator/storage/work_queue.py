"""SQLite-backed work queue for at-least-once issue processing.

B8a: Work Queue Schema & Admission.

Architecture notes:
- queue.db is a separate SQLite store from coordination.db for blast-radius isolation.
- enqueue_issue() uses a 24h TTL lock (issue_lock table) via ON CONFLICT ... WHERE
  locked_until < datetime('now') to prevent duplicate webhook spam while still
  allowing re-enqueue after the lock expires.
- dequeue_issue() is a single atomic UPDATE ... RETURNING query that picks the oldest
  pending job OR an expired lease (up to 3 retries). This eliminates race conditions
  between concurrent workers without needing external locks.
- poison pill protection: jobs with retries >= 3 are skipped by the dequeue subquery.
"""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from orchestrator.storage import _sqlite_connect, _wal_write

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS issue_lock (
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    locked_until TEXT,
    PRIMARY KEY (repo, issue_number)
);

CREATE TABLE IF NOT EXISTS work_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_number INTEGER NOT NULL,
    repo TEXT NOT NULL,
    run_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    retries INTEGER DEFAULT 0,
    payload TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    scheduled_after TEXT,
    lease_expires_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_checkpoint (
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('scout','architect','executor','validator','apply')),
    output TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (run_id, stage)
);
"""


def init_queue_db(db_path: Path) -> sqlite3.Connection:
    conn = _sqlite_connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def _generate_run_id() -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(4)
    return f"run_{now}_{rand}"


def enqueue_issue(
    conn: sqlite3.Connection,
    issue_number: int,
    repo: str,
    payload: str,
) -> Optional[str]:
    run_id = _generate_run_id()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT INTO issue_lock (repo, issue_number, run_id, locked_until) "
            "VALUES (?, ?, ?, datetime('now', '+24 hours')) "
            "ON CONFLICT (repo, issue_number) DO UPDATE SET "
            "run_id = excluded.run_id, "
            "locked_until = excluded.locked_until "
            "WHERE locked_until < datetime('now')",
            (repo, issue_number, run_id),
        )
        if cur.rowcount == 0:
            conn.execute("ROLLBACK")
            return None
        conn.execute(
            "INSERT INTO work_queue (issue_number, repo, run_id, status, created_at, payload) "
            "VALUES (?, ?, ?, 'pending', datetime('now'), ?)",
            (issue_number, repo, run_id, payload),
        )
        conn.execute("COMMIT")
        return run_id
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        return None
    except Exception:
        conn.execute("ROLLBACK")
        raise


def dequeue_issue(conn: sqlite3.Connection) -> Optional[dict]:
    cur = conn.execute(
        "UPDATE work_queue SET "
        "status = 'processing', "
        "started_at = datetime('now'), "
        "lease_expires_at = datetime('now', '+1 hour'), "
        "retries = CASE WHEN status = 'processing' THEN retries + 1 ELSE retries END "
        "WHERE id = ("
        "SELECT id FROM work_queue "
        "WHERE status = 'pending' "
        "OR (status = 'processing' AND lease_expires_at <= datetime('now') AND retries < 3) "
        "ORDER BY created_at ASC, id ASC LIMIT 1"
        ") "
        "RETURNING run_id, issue_number, repo, payload, retries"
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# B8b: State-machine worker loop with checkpoint resume.
# ---------------------------------------------------------------------------

STAGES = ["scout", "architect", "executor", "validator", "apply"]
LLM_STAGES = {"scout", "architect", "executor", "validator"}
BACKOFF_MINUTES = [0, 5, 15]
CB_SLEEP_MAX = 30
POLL_IDLE_SECONDS = 5


def _is_deterministic(exc: BaseException) -> bool:
    """Classify exceptions into deterministic (→ dead_letter) vs transient (→ backoff).
    Imports are inline to keep this module importable without optional schemas."""
    from orchestrator.exceptions import (
        GitConflictError,
        PatchApplyError,
    )
    from orchestrator.llm.parser import SchemaValidationError

    deterministic: tuple = (
        ValueError,
        TypeError,
        KeyError,
        FileNotFoundError,
        PatchApplyError,
        GitConflictError,
        SchemaValidationError,
    )
    return isinstance(exc, deterministic)


def _cb_open_sleep_seconds(conn_coord: sqlite3.Connection) -> float:
    """Pure read of cb_state. Returns 0.0 when no provider is OPEN, else
    min(recovery_timeout, CB_SLEEP_MAX). NEVER sleeps — caller owns timing."""
    try:
        rows = conn_coord.execute(
            "SELECT recovery_timeout FROM cb_state WHERE state = 'open'"
        ).fetchall()
    except sqlite3.OperationalError:
        return 0.0
    if not rows:
        return 0.0
    timeouts = [r["recovery_timeout"] or 0.0 for r in rows]
    return float(min(min(timeouts), CB_SLEEP_MAX))


def _hydrate_workspace(run_id: str, workspace: Any, store: Any) -> Path:
    """Phase 0 hydrate. Best-effort recovery of run.json/risk_gate.json.
    Residual risk: events.jsonl is NOT recovered (append-only observability log)."""
    run_dir = workspace.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("run.json", "risk_gate.json"):
        try:
            data = store.read(f"{run_id}/{name}")
        except FileNotFoundError:
            continue
        except Exception:
            continue
        run_dir.joinpath(name).write_text(data, encoding="utf-8")
    return run_dir


_ARTIFACT_NAMES = {
    "scout": "findings.json",
    "architect": "plan.json",
    "validator": "validation.json",
}


def _hydrate_stage(
    run_id: str, stage: str, checkpoint_output: str, workspace: Any, store: Any
) -> None:
    """STRICT re-hydration. Raises on any store/checksum failure."""
    envelope = json.loads(checkpoint_output)
    name = "patch.diff" if stage == "executor" else _ARTIFACT_NAMES[stage]
    if "ref" in envelope:
        data = store.read(envelope["ref"])
        if "checksum" in envelope:
            actual = hashlib.sha256(data.encode("utf-8")).hexdigest()
            if actual != envelope["checksum"]:
                raise RuntimeError(
                    f"checksum mismatch hydrating {stage}/{name}: "
                    f"expected={envelope['checksum']} actual={actual}"
                )
    else:
        data = checkpoint_output
    workspace.write_artifact(run_id, name, data)


def _ensure_clone(payload: dict, workspace: Any, run_id: str) -> Path:
    """Clone payload['clone_url'] into runs/{run_id}/repo (idempotent)."""
    repo_path = workspace.run_dir(run_id) / "repo"
    if (repo_path / ".git").exists():
        return repo_path
    repo_path.mkdir(parents=True, exist_ok=True)
    clone_url = payload.get("clone_url")
    if not clone_url:
        raise ValueError(f"payload missing 'clone_url' for run {run_id}")
    subprocess.run(
        ["git", "clone", clone_url, str(repo_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return repo_path


def _run_llm_stage(
    stage: str,
    run_id: str,
    repo_path: Path,
    workspace: Any,
    prior_outputs: dict,
) -> BaseModel:
    """Execute one LLM stage. Signatures match the existing agent modules verbatim."""
    from orchestrator.agents.architect import run as run_architect
    from orchestrator.agents.executor import run as run_executor
    from orchestrator.agents.scout import run as run_scout
    from orchestrator.agents.validator import run as run_validator
    from orchestrator.schemas.config import TargetConfig

    config = TargetConfig.load(target_path=repo_path, workspace_path=workspace.root)
    staging = workspace.staging_dir_for_run(run_id)

    if stage == "scout":
        out, _ = run_scout(config, run_id=run_id, trace_id=run_id)
    elif stage == "architect":
        out, _ = run_architect(prior_outputs["scout"], config, run_id=run_id, trace_id=run_id)
    elif stage == "executor":
        out, _ = run_executor(prior_outputs["architect"], config, staging_dir=staging)
    elif stage == "validator":
        out, _ = run_validator(config=config, staging_dir=staging)
    else:
        raise ValueError(f"unknown LLM stage: {stage}")
    return out


def _checkpoint_write(
    conn_queue: sqlite3.Connection, run_id: str, stage: str, output_blob: str
) -> None:
    conn_queue.execute(
        "INSERT OR REPLACE INTO pipeline_checkpoint (run_id, stage, output) "
        "VALUES (?, ?, ?)",
        (run_id, stage, output_blob),
    )
    conn_queue.commit()


def _execute_apply_with_checkpoints(
    run_id: str,
    issue_number: int,
    repo_path: Path,
    workspace: Any,
    github: Any,
    store: Any,
) -> None:
    """Headless 5-phase apply with apply.json WAL.

    Branch is `patchforge/run_{run_id}/issue_{issue_number}` per invariant.
    Does NOT call commands.apply.execute() — that path is Typer-driven and
    hardcodes the wrong branch shape.
    """
    from orchestrator.agents.validator import run as run_validator
    from orchestrator.exceptions import PatchApplyError
    from orchestrator.git import (
        apply_patch,
        create_controlled_branch,
        current_head,
        delete_local_branch,
        force_reset_apply,
        git_push,
        push_delete_remote,
    )
    from orchestrator.schemas.artifacts import ApplyResult
    from orchestrator.schemas.config import TargetConfig

    run_dir = workspace.run_dir(run_id)
    wal_path = run_dir / "apply.json"
    branch = f"patchforge/run_{run_id}/issue_{issue_number}"

    # ---- Recovery: if apply.json exists, roll back per its status. ----------
    if wal_path.exists():
        wal: Optional[ApplyResult]
        try:
            wal = ApplyResult.model_validate_json(wal_path.read_text(encoding="utf-8"))
        except Exception:
            wal = None
        if wal is not None and wal.status != "applied":
            if wal.status == "pr_created" and wal.pr_number:
                try:
                    github.close_pr(wal.pr_number)
                except Exception:
                    pass
            if wal.status in ("pr_created", "pushed_remote"):
                try:
                    push_delete_remote(repo_path, branch)
                except Exception:
                    pass
            if wal.pre_apply_head:
                try:
                    force_reset_apply(repo_path, wal.pre_apply_head)
                except Exception:
                    pass
            try:
                delete_local_branch(repo_path, branch, force=True)
            except Exception:
                pass
        try:
            wal_path.unlink()
        except OSError:
            pass

    # ---- Phase 0: fresh entry --------------------------------------------
    pre_apply_head = current_head(repo_path)
    apply_result = ApplyResult(
        run_id=run_id,
        applied_at=datetime.now(timezone.utc),
        branch=branch,
        success=False,
        pre_apply_head=pre_apply_head,
        status="applying",
    )
    _wal_write(apply_result, wal_path)

    # ---- Phase 1: branch + apply + post-apply validation -----------------
    br = create_controlled_branch(repo_path, branch)
    if br.return_code != 0:
        raise PatchApplyError(f"create_controlled_branch failed: {br.stderr}")

    patch_path = run_dir / "patch.diff"
    ap = apply_patch(repo_path, patch_path)
    if ap.return_code != 0:
        try:
            force_reset_apply(repo_path, pre_apply_head)
            delete_local_branch(repo_path, branch, force=True)
        finally:
            apply_result.rolled_back = True
            apply_result.error = ap.stderr
            _wal_write(apply_result, wal_path)
        raise PatchApplyError(f"git apply failed: {ap.stderr}")

    # Post-apply validation against the freshly applied tree.
    config = TargetConfig.load(target_path=repo_path, workspace_path=workspace.root)
    try:
        val_out, _ = run_validator(config=config)
    except Exception:
        val_out = None
    if val_out is not None and not val_out.overall_passed:
        try:
            force_reset_apply(repo_path, pre_apply_head)
            delete_local_branch(repo_path, branch, force=True)
        finally:
            apply_result.rolled_back = True
            apply_result.error = "post-apply validation failed"
            _wal_write(apply_result, wal_path)
        raise PatchApplyError("post-apply validation failed")

    # ---- Phase 2: commit ------------------------------------------------
    commit_msg = f"PatchForge: {run_id} [skip ci]"
    cr = subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-am", commit_msg],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if cr.returncode != 0:
        raise PatchApplyError(f"git commit failed: {cr.stderr}")
    apply_result.status = "committed_local"
    _wal_write(apply_result, wal_path)

    # ---- Phase 3: push --------------------------------------------------
    pr = git_push(repo_path, branch)
    if pr.return_code != 0:
        raise RuntimeError(f"git push failed: {pr.stderr}")  # transient → retry
    apply_result.status = "pushed_remote"
    _wal_write(apply_result, wal_path)

    # ---- Phase 4: PR ----------------------------------------------------
    pr_obj = github.create_pr(
        title=f"PatchForge: {run_id}",
        body=f"Automated patch from PatchForge run `{run_id}` (issue #{issue_number}).",
        head=branch,
        base="main",
    )
    apply_result.pr_number = pr_obj.number
    apply_result.status = "pr_created"
    _wal_write(apply_result, wal_path)

    # ---- Phase 5: final -------------------------------------------------
    apply_result.success = True
    apply_result.status = "applied"
    apply_result.applied_at = datetime.now(timezone.utc)
    _wal_write(apply_result, wal_path)


def _execute_pipeline_with_resume(
    run_id: str,
    payload: str,
    issue_number: int,
    repo: str,
    conn_queue: sqlite3.Connection,
    conn_coord: sqlite3.Connection,
    workspace: Any,
    github: Any,
    store: Any,
) -> None:
    from orchestrator.exceptions import PatchApplyError
    from orchestrator.risk import check_patch_gate
    from orchestrator.schemas.architect_output import ArchitectOutput
    from orchestrator.schemas.executor_output import ExecutorOutput
    from orchestrator.schemas.scout_output import ScoutOutput
    from orchestrator.schemas.validator_output import ValidatorOutput

    payload_dict = json.loads(payload)  # ValueError → deterministic → dead_letter

    _hydrate_workspace(run_id, workspace, store)
    repo_path = _ensure_clone(payload_dict, workspace, run_id)
    run_metadata = workspace.read_run_json(run_id)
    prior_outputs: dict[str, BaseModel] = {}
    inline_model = {
        "scout": ScoutOutput,
        "architect": ArchitectOutput,
        "validator": ValidatorOutput,
    }

    for stage in STAGES:
        if stage == "apply":
            _execute_apply_with_checkpoints(
                run_id, issue_number, repo_path, workspace, github, store
            )
            continue

        existing = conn_queue.execute(
            "SELECT output FROM pipeline_checkpoint WHERE run_id=? AND stage=?",
            (run_id, stage),
        ).fetchone()
        if existing is not None:
            _hydrate_stage(run_id, stage, existing[0], workspace, store)
            if stage in inline_model:
                envelope = json.loads(existing[0])
                if "ref" not in envelope:
                    prior_outputs[stage] = inline_model[stage].model_validate_json(existing[0])
            continue

        out = _run_llm_stage(stage, run_id, repo_path, workspace, prior_outputs)
        prior_outputs[stage] = out

        if stage == "executor":
            assert isinstance(out, ExecutorOutput)
            diffs = [c.diff for c in (out.applied + out.pending_review) if c.diff]
            patch_text = "\n".join(diffs)
            risk_result = check_patch_gate(run_metadata, patch_text, workspace_mgr=workspace)
            if not risk_result.passed:
                raise PatchApplyError(
                    f"Risk gate blocked: {'; '.join(risk_result.reasons)}"
                )
            ref = store.write(f"{run_id}/patch.diff", patch_text).ref
            workspace.write_artifact(run_id, "patch.diff", patch_text)
            checksum = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
            _checkpoint_write(
                conn_queue,
                run_id,
                "executor",
                json.dumps({"ref": ref, "checksum": checksum}),
            )
        else:
            name = _ARTIFACT_NAMES[stage]
            data = out.model_dump_json()
            workspace.write_artifact(run_id, name, data)
            _checkpoint_write(conn_queue, run_id, stage, data)


def worker_loop(
    queue_db: Path,
    coord_db: Path,
    workspace: Any,
    store: Any,
    github: Any,
    *,
    worker_id: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
    iterations: Optional[int] = None,
) -> None:
    """Headless worker loop. Polls queue.db, drives the pipeline, persists
    checkpoints, handles backpressure and retries.

    Test seams:
      stop_event — loop exits when set.
      iterations — None = forever; otherwise run this many iterations and return.
    """
    from orchestrator.circuit_breaker import CircuitBreakerOpenError, circuit_breaker_for
    from orchestrator.storage.lock import SqliteCircuitBreakerStore

    conn_queue = _sqlite_connect(queue_db)
    conn_coord = _sqlite_connect(coord_db)

    cb_store = SqliteCircuitBreakerStore(coord_db.parent)
    for provider in ("gemini", "claude", "groq"):
        circuit_breaker_for(provider, store=cb_store)

    completed_iters = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        if iterations is not None and completed_iters >= iterations:
            return
        completed_iters += 1

        try:
            sleep_for = _cb_open_sleep_seconds(conn_coord)
            if sleep_for > 0:
                time.sleep(sleep_for)
                continue

            row = dequeue_issue(conn_queue)
            if row is None:
                time.sleep(POLL_IDLE_SECONDS)
                continue

            run_id = row["run_id"]
            issue_number = row["issue_number"]
            branch = f"patchforge/run_{run_id}/issue_{issue_number}"
            existing_pr = github.get_pr_for_branch(branch)
            if existing_pr is not None:
                conn_queue.execute(
                    "UPDATE work_queue SET status='completed', "
                    "completed_at=datetime('now') WHERE run_id=?",
                    (run_id,),
                )
                conn_queue.commit()
                continue

            try:
                _execute_pipeline_with_resume(
                    run_id,
                    row["payload"],
                    issue_number,
                    row["repo"],
                    conn_queue,
                    conn_coord,
                    workspace,
                    github,
                    store,
                )
                conn_queue.execute(
                    "UPDATE work_queue SET status='completed', "
                    "completed_at=datetime('now') WHERE run_id=?",
                    (run_id,),
                )
            except CircuitBreakerOpenError:
                conn_queue.execute(
                    "UPDATE work_queue SET status='pending', "
                    "scheduled_after=datetime('now', '+' || ? || ' seconds') "
                    "WHERE run_id=?",
                    (random.randint(15, 45), run_id),
                )
            except Exception as e:
                if _is_deterministic(e) or row["retries"] >= 2:
                    conn_queue.execute(
                        "UPDATE work_queue SET status='dead_letter', "
                        "error=? WHERE run_id=?",
                        (repr(e), run_id),
                    )
                else:
                    backoff = BACKOFF_MINUTES[row["retries"]]
                    conn_queue.execute(
                        "UPDATE work_queue SET status='pending', retries=retries+1, "
                        "scheduled_after=datetime('now', '+' || ? || ' minutes') "
                        "WHERE run_id=?",
                        (backoff, run_id),
                    )
            conn_queue.commit()
        except Exception as outer_e:
            import sys

            sys.stderr.write(f"Worker loop fatal error: {outer_e!r}\n")
            time.sleep(10)
