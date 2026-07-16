"""Tests for B8b worker_loop and helpers in orchestrator.storage.work_queue."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.exceptions import CircuitBreakerOpenError, PatchApplyError
from orchestrator.schemas.artifacts import ApplyResult, RunMetadata
from orchestrator.schemas.executor_output import ExecutorOutput, FileChange, TaskStatus
from orchestrator.storage import _sqlite_connect
from orchestrator.storage.lock import SqliteCircuitBreakerStore
from orchestrator.storage.work_queue import (
    _cb_open_sleep_seconds,
    _ensure_clone,
    _execute_apply_with_checkpoints,
    _hydrate_stage,
    _is_deterministic,
    init_queue_db,
    worker_loop,
)
from orchestrator.workspace import WorkspaceManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def qdb_path(tmp_path: Path) -> Path:
    p = tmp_path / "queue.db"
    init_queue_db(p).close()
    return p


@pytest.fixture
def cdb_path(tmp_path: Path) -> Path:
    SqliteCircuitBreakerStore(tmp_path)
    return tmp_path / "coordination.db"


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceManager:
    ws = WorkspaceManager(tmp_path / "ws", worker_id="test")
    ws.setup()
    return ws


@pytest.fixture
def fake_github() -> MagicMock:
    gh = MagicMock()
    gh.get_pr_for_branch.return_value = None
    gh.create_pr.return_value = MagicMock(number=99)
    gh.close_pr.return_value = None
    return gh


@pytest.fixture
def fake_store() -> MagicMock:
    s = MagicMock()
    s.write.return_value = MagicMock(ref="local-ref")
    # Default: reads miss, so best-effort _hydrate_workspace doesn't clobber
    # workspace-seeded run.json. Per-test overrides side_effect for specific refs.
    s.read.side_effect = FileNotFoundError
    return s


def _seed_run(workspace: WorkspaceManager, run_id: str, repo_path: Path) -> RunMetadata:
    workspace.create_run_directory(run_id)
    meta = RunMetadata(
        run_id=run_id,
        target_path=str(repo_path),
        workspace_path=str(workspace.root),
        base_commit="0" * 40,
        branch="main",
        v1_supported=True,
        max_files=10,
        max_diff_lines=10_000,
    )
    workspace.write_run_json(run_id, meta)
    return meta


def _seed_job(qdb_path: Path, payload: dict, *, retries: int = 0, run_id: str = "run_test"):
    conn = _sqlite_connect(qdb_path)
    conn.execute(
        "INSERT INTO work_queue (issue_number, repo, run_id, status, created_at, "
        "payload, retries) VALUES (?, ?, ?, 'pending', datetime('now'), ?, ?)",
        (
            payload.get("issue_number", 1),
            payload.get("repo", "o/r"),
            run_id,
            json.dumps(payload),
            retries,
        ),
    )
    conn.commit()
    conn.close()
    return run_id


# ---------------------------------------------------------------------------
# Helper-level tests
# ---------------------------------------------------------------------------


def test_is_deterministic_classifies_value_error():
    assert _is_deterministic(ValueError("bad"))
    assert _is_deterministic(PatchApplyError("blocked"))
    assert not _is_deterministic(ConnectionError("net"))
    assert not _is_deterministic(TimeoutError("slow"))


def test_cb_open_sleep_seconds_returns_zero_when_no_open(cdb_path: Path):
    conn = _sqlite_connect(cdb_path)
    assert _cb_open_sleep_seconds(conn) == 0.0


def test_cb_open_sleep_seconds_returns_min_capped(cdb_path: Path):
    conn = _sqlite_connect(cdb_path)
    conn.execute(
        "INSERT OR REPLACE INTO cb_state (provider, state, failures, "
        "last_failure_at, recovery_timeout) VALUES (?, 'open', 1, 0, ?)",
        ("gemini", 120.0),
    )
    assert _cb_open_sleep_seconds(conn) == 30.0


def test_ensure_clone_skips_when_dotgit_exists(workspace, monkeypatch):
    run_id = "run_skipclone"
    repo_path = workspace.run_dir(run_id) / "repo"
    (repo_path / ".git").mkdir(parents=True)

    called = {"n": 0}

    def fake_run(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("git clone should not be invoked when .git exists")

    monkeypatch.setattr("orchestrator.storage.work_queue.subprocess.run", fake_run)
    out = _ensure_clone({"clone_url": "https://example/x.git"}, workspace, run_id)
    assert out == repo_path
    assert called["n"] == 0


def test_hydrate_stage_raises_on_store_failure(workspace, fake_store):
    run_id = "run_hyd_err"
    _seed_run(workspace, run_id, workspace.root)
    fake_store.read.side_effect = IOError("network blip")
    envelope = json.dumps({"ref": "x/patch.diff", "checksum": "abc"})
    with pytest.raises(IOError):
        _hydrate_stage(run_id, "executor", envelope, workspace, fake_store)


def test_hydrate_stage_checksum_mismatch(workspace, fake_store):
    run_id = "run_hyd_chk"
    _seed_run(workspace, run_id, workspace.root)
    fake_store.read.side_effect = None
    fake_store.read.return_value = "PAYLOAD"
    bad_checksum = "deadbeef"
    envelope = json.dumps({"ref": "x/patch.diff", "checksum": bad_checksum})
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _hydrate_stage(run_id, "executor", envelope, workspace, fake_store)


def test_hydrate_stage_inline_json_writes_local(workspace, fake_store):
    run_id = "run_hyd_inline"
    _seed_run(workspace, run_id, workspace.root)
    inline = '{"hotspots": [], "recommended_order": [], "risks": [], "summary": "x"}'
    _hydrate_stage(run_id, "scout", inline, workspace, fake_store)
    assert (workspace.run_dir(run_id) / "findings.json").exists()


# ---------------------------------------------------------------------------
# worker_loop integration tests
# ---------------------------------------------------------------------------


def test_worker_loop_branch_idempotency_skips_pipeline(
    qdb_path, cdb_path, workspace, fake_store, fake_github
):
    """When github.get_pr_for_branch returns an open PR, the pipeline must not run."""
    run_id = _seed_job(qdb_path, {"issue_number": 5, "clone_url": "https://example/x.git"})
    fake_github.get_pr_for_branch.return_value = MagicMock(number=42)

    worker_loop(qdb_path, cdb_path, workspace, fake_store, fake_github, iterations=1)

    conn = _sqlite_connect(qdb_path)
    row = conn.execute("SELECT status FROM work_queue WHERE run_id=?", (run_id,)).fetchone()
    assert row["status"] == "completed"
    fake_github.create_pr.assert_not_called()


def test_worker_loop_cb_open_yields_without_burning_retry(
    qdb_path, cdb_path, workspace, fake_store, fake_github, monkeypatch
):
    """If a provider is OPEN, the loop sleeps and does NOT dequeue."""
    _seed_job(qdb_path, {"issue_number": 6, "clone_url": "x"})
    conn_coord = _sqlite_connect(cdb_path)
    conn_coord.execute(
        "INSERT OR REPLACE INTO cb_state (provider, state, failures, "
        "last_failure_at, recovery_timeout) VALUES ('gemini', 'open', 1, 0, 60)"
    )
    conn_coord.close()

    slept: list[float] = []
    monkeypatch.setattr("orchestrator.storage.work_queue.time.sleep", lambda s: slept.append(s))
    worker_loop(qdb_path, cdb_path, workspace, fake_store, fake_github, iterations=1)

    conn = _sqlite_connect(qdb_path)
    row = conn.execute("SELECT status, retries FROM work_queue").fetchone()
    assert row["status"] == "pending"
    assert row["retries"] == 0
    assert slept and slept[0] <= 30


def test_worker_loop_cb_open_raised_mid_pipeline(
    qdb_path, cdb_path, workspace, fake_store, fake_github, monkeypatch
):
    """CircuitBreakerOpenError mid-pipeline yields to pending without burning a retry."""
    run_id = _seed_job(qdb_path, {"issue_number": 7, "clone_url": "x"})

    def raise_cb(*args, **kwargs):
        raise CircuitBreakerOpenError(provider="gemini", state="open", retry_after=0)

    monkeypatch.setattr("orchestrator.storage.work_queue._execute_pipeline_with_resume", raise_cb)
    worker_loop(qdb_path, cdb_path, workspace, fake_store, fake_github, iterations=1)

    conn = _sqlite_connect(qdb_path)
    row = conn.execute(
        "SELECT status, retries FROM work_queue WHERE run_id=?", (run_id,)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["retries"] == 0


def test_worker_loop_deterministic_error_to_dead_letter(
    qdb_path, cdb_path, workspace, fake_store, fake_github, monkeypatch
):
    """A deterministic exception (ValueError) goes directly to dead_letter on first failure."""
    run_id = _seed_job(qdb_path, {"issue_number": 8, "clone_url": "x"})

    def raise_value(*args, **kwargs):
        raise ValueError("bad payload")

    monkeypatch.setattr(
        "orchestrator.storage.work_queue._execute_pipeline_with_resume", raise_value
    )
    worker_loop(qdb_path, cdb_path, workspace, fake_store, fake_github, iterations=1)

    conn = _sqlite_connect(qdb_path)
    row = conn.execute("SELECT status, error FROM work_queue WHERE run_id=?", (run_id,)).fetchone()
    assert row["status"] == "dead_letter"
    assert "bad payload" in row["error"]


def test_worker_loop_transient_error_backoff_then_dead_letter(
    qdb_path, cdb_path, workspace, fake_store, fake_github, monkeypatch
):
    """Three transient failures → dead_letter. retries increments exactly once per attempt."""
    run_id = _seed_job(qdb_path, {"issue_number": 9, "clone_url": "x"})

    def raise_conn(*args, **kwargs):
        raise ConnectionError("net")

    monkeypatch.setattr("orchestrator.storage.work_queue._execute_pipeline_with_resume", raise_conn)
    # Lease expiry path is not tested here; we drive iterations directly and
    # reset status to pending between iterations is done by the loop itself.
    for _ in range(3):
        # Make the job dequeueable again by clearing scheduled_after.
        conn = _sqlite_connect(qdb_path)
        conn.execute("UPDATE work_queue SET scheduled_after=NULL WHERE run_id=?", (run_id,))
        conn.close()
        worker_loop(qdb_path, cdb_path, workspace, fake_store, fake_github, iterations=1)

    conn = _sqlite_connect(qdb_path)
    row = conn.execute(
        "SELECT status, retries FROM work_queue WHERE run_id=?", (run_id,)
    ).fetchone()
    assert row["status"] == "dead_letter"


def test_risk_gate_blocks_executor_patch(
    qdb_path, cdb_path, workspace, fake_store, fake_github, monkeypatch, tmp_path
):
    """Executor whose patch trips the risk gate → PatchApplyError → dead_letter."""
    run_id = _seed_job(qdb_path, {"issue_number": 10, "clone_url": "x"})

    repo_path = workspace.run_dir(run_id) / "repo"
    (repo_path / ".git").mkdir(parents=True)
    _seed_run(workspace, run_id, repo_path)

    # Hand-seed scout+architect checkpoints so the loop reaches executor.
    qconn = _sqlite_connect(qdb_path)
    qconn.execute(
        "INSERT INTO pipeline_checkpoint (run_id, stage, output) VALUES (?, 'scout', ?)",
        (run_id, '{"hotspots": [], "recommended_order": [], "risks": [], "summary": "s"}'),
    )
    qconn.execute(
        "INSERT INTO pipeline_checkpoint (run_id, stage, output) VALUES (?, 'architect', ?)",
        (
            run_id,
            '{"validated_findings": [], "false_positives": [], "systemic_risks": [], "implementation_plan": [], "blockers": []}',
        ),
    )
    qconn.commit()
    qconn.close()

    diff = "\n".join(
        f"diff --git a/f{i}.py b/f{i}.py\n+++ b/f{i}.py\n@@ +1 @@\n+x" for i in range(50)
    )
    fake_executor_out = ExecutorOutput(
        applied=[FileChange(task_id="t1", file="f.py", status=TaskStatus.APPLIED, diff=diff)],
    )

    def fake_run_llm_stage(stage, *a, **kw):
        if stage == "validator":
            from orchestrator.schemas.validator_output import ValidatorOutput

            return ValidatorOutput(overall_passed=True, tools=[])
        return fake_executor_out

    monkeypatch.setattr("orchestrator.storage.work_queue._run_llm_stage", fake_run_llm_stage)
    # Force risk gate to block.
    monkeypatch.setattr(
        "orchestrator.risk.check_patch_gate",
        lambda meta, diff, workspace_mgr=None: MagicMock(passed=False, reasons=["too big"]),
    )

    worker_loop(qdb_path, cdb_path, workspace, fake_store, fake_github, iterations=1)

    conn = _sqlite_connect(qdb_path)
    row = conn.execute("SELECT status, error FROM work_queue WHERE run_id=?", (run_id,)).fetchone()
    assert row["status"] == "dead_letter"
    assert "Risk gate" in row["error"]
    # No executor checkpoint written.
    cp = conn.execute(
        "SELECT 1 FROM pipeline_checkpoint WHERE run_id=? AND stage='executor'",
        (run_id,),
    ).fetchone()
    assert cp is None


def test_worker_loop_resumes_from_checkpoint(
    qdb_path, cdb_path, workspace, fake_store, fake_github, monkeypatch
):
    """Seeded checkpoints for scout/architect/executor → loop must skip them and only run validator+apply."""
    run_id = _seed_job(qdb_path, {"issue_number": 11, "clone_url": "x"})
    repo_path = workspace.run_dir(run_id) / "repo"
    (repo_path / ".git").mkdir(parents=True)
    _seed_run(workspace, run_id, repo_path)

    qconn = _sqlite_connect(qdb_path)
    qconn.execute(
        "INSERT INTO pipeline_checkpoint (run_id, stage, output) VALUES (?, 'scout', ?)",
        (run_id, '{"hotspots": [], "recommended_order": [], "risks": [], "summary": "s"}'),
    )
    qconn.execute(
        "INSERT INTO pipeline_checkpoint (run_id, stage, output) VALUES (?, 'architect', ?)",
        (
            run_id,
            '{"validated_findings": [], "false_positives": [], "systemic_risks": [], "implementation_plan": [], "blockers": []}',
        ),
    )
    patch_text = "diff --git a/x b/x\n+++ b/x\n@@ +1 @@\n+y"
    checksum = hashlib.sha256(patch_text.encode()).hexdigest()

    def store_read(ref):
        if ref.endswith("/patch.diff"):
            return patch_text
        raise FileNotFoundError(ref)

    fake_store.read.side_effect = store_read
    qconn.execute(
        "INSERT INTO pipeline_checkpoint (run_id, stage, output) VALUES (?, 'executor', ?)",
        (run_id, json.dumps({"ref": "x/patch.diff", "checksum": checksum})),
    )
    qconn.commit()
    qconn.close()

    called_stages: list[str] = []

    def fake_run_llm(stage, *a, **kw):
        called_stages.append(stage)
        if stage == "validator":
            from orchestrator.schemas.validator_output import ValidatorOutput

            return ValidatorOutput(overall_passed=True, tools=[])
        raise AssertionError(f"checkpointed stage {stage} should NOT run")

    monkeypatch.setattr("orchestrator.storage.work_queue._run_llm_stage", fake_run_llm)
    monkeypatch.setattr(
        "orchestrator.storage.work_queue._execute_apply_with_checkpoints",
        lambda *a, **kw: None,
    )

    worker_loop(qdb_path, cdb_path, workspace, fake_store, fake_github, iterations=1)

    assert called_stages == ["validator"]
    conn = _sqlite_connect(qdb_path)
    row = conn.execute("SELECT status FROM work_queue WHERE run_id=?", (run_id,)).fetchone()
    assert row["status"] == "completed"


# ---------------------------------------------------------------------------
# Apply WAL recovery tests (call _execute_apply_with_checkpoints directly)
# ---------------------------------------------------------------------------


def _seed_apply_wal(workspace, run_id, status, *, pr_number=None, pre_apply_head="abc123"):
    workspace.create_run_directory(run_id)
    _seed_run(workspace, run_id, workspace.root)
    wal = ApplyResult(
        run_id=run_id,
        applied_at=datetime.now(timezone.utc),
        branch=f"patchforge/run_{run_id}/issue_1",
        success=False,
        pre_apply_head=pre_apply_head,
        status=status,
        pr_number=pr_number,
    )
    (workspace.run_dir(run_id) / "apply.json").write_text(
        wal.model_dump_json(indent=2), encoding="utf-8"
    )


def test_apply_wal_recovery_from_pushed_remote(workspace, fake_github, fake_store, monkeypatch):
    run_id = "run_recover_push"
    _seed_apply_wal(workspace, run_id, "pushed_remote")
    repo_path = workspace.run_dir(run_id) / "repo"
    repo_path.mkdir(parents=True)
    (repo_path / "patch.diff").write_text("", encoding="utf-8")

    calls: list[str] = []
    monkeypatch.setattr(
        "orchestrator.git.push_delete_remote",
        lambda *a, **kw: calls.append("push_delete") or MagicMock(return_code=0),
    )
    monkeypatch.setattr(
        "orchestrator.git.force_reset_apply",
        lambda *a, **kw: calls.append("reset") or MagicMock(return_code=0),
    )
    monkeypatch.setattr(
        "orchestrator.git.delete_local_branch",
        lambda *a, **kw: calls.append("delete_branch") or MagicMock(return_code=0),
    )
    # Make phase 0 fail fast after recovery so we only test the recovery sequence.
    monkeypatch.setattr(
        "orchestrator.git.current_head",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("stop here")),
    )

    with pytest.raises(RuntimeError, match="stop here"):
        _execute_apply_with_checkpoints(run_id, 1, repo_path, workspace, fake_github, fake_store)

    assert "push_delete" in calls
    assert "reset" in calls
    assert "delete_branch" in calls
    fake_github.close_pr.assert_not_called()


def test_apply_wal_recovery_from_pr_created(workspace, fake_github, fake_store, monkeypatch):
    run_id = "run_recover_pr"
    _seed_apply_wal(workspace, run_id, "pr_created", pr_number=42)
    repo_path = workspace.run_dir(run_id) / "repo"
    repo_path.mkdir(parents=True)

    monkeypatch.setattr(
        "orchestrator.git.push_delete_remote", lambda *a, **kw: MagicMock(return_code=0)
    )
    monkeypatch.setattr(
        "orchestrator.git.force_reset_apply", lambda *a, **kw: MagicMock(return_code=0)
    )
    monkeypatch.setattr(
        "orchestrator.git.delete_local_branch", lambda *a, **kw: MagicMock(return_code=0)
    )
    monkeypatch.setattr(
        "orchestrator.git.current_head",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("stop here")),
    )

    with pytest.raises(RuntimeError, match="stop here"):
        _execute_apply_with_checkpoints(run_id, 1, repo_path, workspace, fake_github, fake_store)

    fake_github.close_pr.assert_called_once_with(42)


def test_apply_pr_body_includes_triggered_by(
    workspace, fake_github, fake_store, monkeypatch, tmp_path
):
    """#241: PR body created in Phase 4 must include the Triggered by line
    when triggered_by is passed from _execute_pipeline_with_resume."""
    from orchestrator.schemas.validator_output import ValidatorOutput

    run_id = "run_pr_body"
    workspace.create_run_directory(run_id)
    repo_path = workspace.run_dir(run_id) / "repo"
    repo_path.mkdir(parents=True)
    (workspace.run_dir(run_id) / "patch.diff").write_text("dummy patch\n", encoding="utf-8")

    # Mock every subprocess/git touchpoint reached before Phase 4.
    monkeypatch.setattr("orchestrator.git.current_head", lambda *a, **kw: "0" * 40)
    monkeypatch.setattr(
        "orchestrator.git.create_controlled_branch",
        lambda *a, **kw: MagicMock(return_code=0, stderr=""),
    )
    monkeypatch.setattr(
        "orchestrator.git.apply_patch",
        lambda *a, **kw: MagicMock(return_code=0, stderr=""),
    )
    monkeypatch.setattr(
        "orchestrator.git.git_push",
        lambda *a, **kw: MagicMock(return_code=0, stderr=""),
    )
    monkeypatch.setattr(
        "orchestrator.storage.work_queue.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stderr="", stdout=""),
    )
    # Skip Phase 1 post-apply validation (TargetConfig.load + validator).
    monkeypatch.setattr(
        "orchestrator.schemas.config.TargetConfig.load",
        classmethod(lambda cls, **kw: MagicMock()),
    )
    monkeypatch.setattr(
        "orchestrator.agents.validator.run",
        lambda **kw: (
            ValidatorOutput(overall_passed=True, checks=[], model_used_for_summary="test"),
            {},
        ),
    )

    _execute_apply_with_checkpoints(
        run_id,
        1,
        repo_path,
        workspace,
        fake_github,
        fake_store,
        triggered_by="github:octocat",
    )

    fake_github.create_pr.assert_called_once()
    body = fake_github.create_pr.call_args.kwargs["body"]
    assert "**Triggered by:** github:octocat" in body


def test_apply_pr_body_omits_triggered_by_when_none(
    workspace, fake_github, fake_store, monkeypatch
):
    """When triggered_by is None (default), the PR body must not add an
    empty provenance line."""
    from orchestrator.schemas.validator_output import ValidatorOutput

    run_id = "run_pr_body_none"
    workspace.create_run_directory(run_id)
    repo_path = workspace.run_dir(run_id) / "repo"
    repo_path.mkdir(parents=True)
    (workspace.run_dir(run_id) / "patch.diff").write_text("dummy patch\n", encoding="utf-8")

    monkeypatch.setattr("orchestrator.git.current_head", lambda *a, **kw: "0" * 40)
    monkeypatch.setattr(
        "orchestrator.git.create_controlled_branch",
        lambda *a, **kw: MagicMock(return_code=0, stderr=""),
    )
    monkeypatch.setattr(
        "orchestrator.git.apply_patch", lambda *a, **kw: MagicMock(return_code=0, stderr="")
    )
    monkeypatch.setattr(
        "orchestrator.git.git_push", lambda *a, **kw: MagicMock(return_code=0, stderr="")
    )
    monkeypatch.setattr(
        "orchestrator.storage.work_queue.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stderr="", stdout=""),
    )
    monkeypatch.setattr(
        "orchestrator.schemas.config.TargetConfig.load",
        classmethod(lambda cls, **kw: MagicMock()),
    )
    monkeypatch.setattr(
        "orchestrator.agents.validator.run",
        lambda **kw: (
            ValidatorOutput(overall_passed=True, checks=[], model_used_for_summary="test"),
            {},
        ),
    )

    _execute_apply_with_checkpoints(run_id, 1, repo_path, workspace, fake_github, fake_store)

    body = fake_github.create_pr.call_args.kwargs["body"]
    assert "Triggered by" not in body
