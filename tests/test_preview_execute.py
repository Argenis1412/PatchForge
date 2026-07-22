"""Tests for preview.execute() — 8 scenarios + 2 cross-cutting invariants.

Invariants verified on every scenario:
A) Preview never modifies the target repo (HEAD + working tree unchanged).
B) Preview always leaves auditable evidence (success or failure artifacts).
"""

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock

import pytest
import typer

from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.schemas.executor_output import (
    ExecutorOutput,
    FileChange,
    TaskStatus,
)
from orchestrator.schemas.git import GitCommandResult
from orchestrator.schemas.risk import RiskGateResult
from orchestrator.schemas.validator_output import ValidatorOutput
from orchestrator.workspace import WorkspaceManager

# ── Helpers ──────────────────────────────────────────────────────────────────


def _init_git_repo(path: Path) -> str:
    """Create a minimal git repo and return its HEAD sha."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "file.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _current_head(path: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _is_clean(path: Path) -> bool:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip() == ""


def _get_branch(path: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _make_plan() -> ArchitectOutput:
    return ArchitectOutput(
        validated_findings=["Finding 1"],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[
            Task(
                task_id="T1",
                title="Fix thing",
                description="Fix the thing",
                files_to_modify=["file.txt"],
                priority="medium",
                effort="low",
                risk_level="low",
            )
        ],
        blockers=[],
    )


def _make_run_metadata(
    run_id: str, target_path: Path, workspace_path: Path, branch: str, base_commit: str
) -> RunMetadata:
    return RunMetadata(
        run_id=run_id,
        target_path=str(target_path),
        workspace_path=str(workspace_path),
        base_commit=base_commit,
        branch=branch,
        status="planned",
        v1_supported=True,
        max_files=10,
        max_diff_lines=500,
    )


@contextmanager
def _mock_validation_workspace() -> Generator:
    """Context manager that yields a mock ValidationWorkspace."""
    ws = MagicMock()
    ws.temporary_root = Path("/tmp/fake-val-ws")
    ws.patch_path = Path("/tmp/fake-val-ws/patch.diff")
    yield ws


def _execute(*args, **kwargs):
    """Lazy-import wrapper to avoid eagerly resolving preview module bindings."""
    from orchestrator.commands.preview import execute

    return execute(*args, **kwargs)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _unload_preview_module():
    """Prevent preview module import from poisoning other test files.

    The preview module binds ``from orchestrator.validation_workspace import
    run_validation_in_copy`` at import time.  Other test files (e.g.
    test_v1_commands) patch at the source module; that patch is invisible to
    the already-bound name.  Evicting the cached module after each test
    forces a fresh import on the next call to ``_execute``.
    """
    import sys

    yield
    sys.modules.pop("orchestrator.commands.preview", None)


@pytest.fixture()
def env(tmp_path: Path):
    """Set up target repo + workspace + run with plan."""
    target = tmp_path / "repo"
    target.mkdir()
    head_sha = _init_git_repo(target)
    branch = _get_branch(target)

    workspace_path = tmp_path / "workspace"
    wm = WorkspaceManager(workspace_path)
    wm.setup()

    run_id = "test-preview-run"
    wm.create_run_directory(run_id)

    meta = _make_run_metadata(run_id, target, workspace_path, branch, head_sha)
    wm.write_run_json(run_id, meta)

    plan = _make_plan()
    wm.write_artifact(run_id, "plan.json", plan.model_dump_json(indent=2))

    return {
        "target": target,
        "workspace_path": workspace_path,
        "wm": wm,
        "run_id": run_id,
        "head_sha": head_sha,
        "branch": branch,
    }


# ── Invariant helpers ────────────────────────────────────────────────────────


def _snapshot_target(target: Path) -> tuple[str, bool]:
    return _current_head(target), _is_clean(target)


def _assert_target_unchanged(target: Path, before: tuple[str, bool]) -> None:
    """Invariant A: preview never modifies the target repo."""
    after = _snapshot_target(target)
    assert before[0] == after[0], f"HEAD changed: {before[0]} → {after[0]}"
    assert before[1] == after[1], f"Working tree cleanliness changed: {before[1]} → {after[1]}"


def _assert_failure_evidence(wm: WorkspaceManager, run_id: str) -> None:
    """Invariant B (failure): run.json status=failed + failure event logged."""
    run_dir = wm.run_dir(run_id)
    run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_json["status"] == "failed", f"Expected status=failed, got {run_json['status']}"

    logs_dir = Path(run_json["workspace_path"]) / "logs"
    pipeline_jsonl = logs_dir / "pipeline.jsonl"
    assert pipeline_jsonl.exists(), "pipeline.jsonl missing — no failure event logged"
    events = pipeline_jsonl.read_text(encoding="utf-8")
    assert "error" in events.lower() or "fail" in events.lower(), (
        "No error/failure event found in pipeline.jsonl"
    )


def _assert_success_evidence(wm: WorkspaceManager, run_id: str) -> None:
    """Invariant B (success): patch.diff + validation.json + run.json status=previewed."""
    run_dir = wm.run_dir(run_id)
    assert (run_dir / "patch.diff").exists(), "patch.diff missing"
    assert (run_dir / "validation.json").exists(), "validation.json missing"
    run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_json["status"] == "previewed", f"Expected status=previewed, got {run_json['status']}"


# ── Scenario 1: Run does not exist ──────────────────────────────────────────


def test_run_not_found(tmp_path: Path) -> None:
    workspace_path = tmp_path / "workspace"
    wm = WorkspaceManager(workspace_path)
    wm.setup()

    target = tmp_path / "repo"
    target.mkdir()
    _init_git_repo(target)
    before = _snapshot_target(target)

    with pytest.raises(typer.Exit) as exc:
        _execute("nonexistent-run", workspace=workspace_path)
    assert exc.value.exit_code == 1

    _assert_target_unchanged(target, before)


# ── Scenario 2: Invalid/corrupt plan ────────────────────────────────────────


def test_invalid_plan(env, monkeypatch: pytest.MonkeyPatch) -> None:
    wm, run_id = env["wm"], env["run_id"]
    target = env["target"]
    before = _snapshot_target(target)

    wm.write_artifact(run_id, "plan.json", "NOT VALID JSON {{{")

    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)

    with pytest.raises(typer.Exit) as exc:
        _execute(run_id, workspace=env["workspace_path"])
    assert exc.value.exit_code == 1

    _assert_target_unchanged(target, before)


# ── Scenario 3: Staging cleanup fails ───────────────────────────────────────


def test_staging_cleanup_fails(env, monkeypatch: pytest.MonkeyPatch) -> None:
    wm, run_id = env["wm"], env["run_id"]
    target = env["target"]
    before = _snapshot_target(target)

    run_dir = wm.run_dir(run_id)
    staging = run_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "old.txt").write_text("stale", encoding="utf-8")

    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)

    def _fail_rmtree(path, **kw):
        raise OSError("Permission denied (simulated)")

    monkeypatch.setattr("shutil.rmtree", _fail_rmtree)

    with pytest.raises(typer.Exit) as exc:
        _execute(run_id, workspace=env["workspace_path"])
    assert exc.value.exit_code == 1

    _assert_target_unchanged(target, before)
    _assert_failure_evidence(wm, run_id)


# ── Scenario 4: Executor fails ──────────────────────────────────────────────


def test_executor_fails(env, monkeypatch: pytest.MonkeyPatch) -> None:
    wm, run_id = env["wm"], env["run_id"]
    target = env["target"]
    before = _snapshot_target(target)

    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)
    monkeypatch.setattr(
        "orchestrator.agents.executor.run",
        MagicMock(side_effect=RuntimeError("LLM timeout")),
    )

    with pytest.raises(typer.Exit) as exc:
        _execute(run_id, workspace=env["workspace_path"])
    assert exc.value.exit_code == 1

    _assert_target_unchanged(target, before)
    _assert_failure_evidence(wm, run_id)


# ── Scenario 5: Empty patch ─────────────────────────────────────────────────


def test_empty_patch(env, monkeypatch: pytest.MonkeyPatch) -> None:
    wm, run_id = env["wm"], env["run_id"]
    target = env["target"]
    before = _snapshot_target(target)

    empty_output = ExecutorOutput(applied=[], pending_review=[], errors=[])

    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)
    monkeypatch.setattr(
        "orchestrator.agents.executor.run",
        MagicMock(return_value=(empty_output, {"cost_usd": 0.0})),
    )

    run_dir = wm.run_dir(run_id)
    (run_dir / "patch.diff").write_text("old stale diff", encoding="utf-8")
    (run_dir / "validation.json").write_text("{}", encoding="utf-8")

    with pytest.raises(typer.Exit) as exc:
        _execute(run_id, workspace=env["workspace_path"])
    assert exc.value.exit_code == 1

    assert not (run_dir / "patch.diff").exists(), "Stale patch.diff should be cleaned"
    assert not (run_dir / "validation.json").exists(), "Stale validation.json should be cleaned"

    _assert_target_unchanged(target, before)
    _assert_failure_evidence(wm, run_id)


# ── Scenario 6: Risk gate blocks ────────────────────────────────────────────


def test_risk_gate_blocks(env, monkeypatch: pytest.MonkeyPatch) -> None:
    wm, run_id = env["wm"], env["run_id"]
    target = env["target"]
    before = _snapshot_target(target)

    diff_text = "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-hello\n+world"
    output = ExecutorOutput(
        applied=[
            FileChange(task_id="T1", file="file.txt", status=TaskStatus.APPLIED, diff=diff_text)
        ],
    )

    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)
    monkeypatch.setattr(
        "orchestrator.agents.executor.run",
        MagicMock(return_value=(output, {"cost_usd": 0.01})),
    )
    monkeypatch.setattr(
        "orchestrator.risk.check_patch_gate",
        MagicMock(
            return_value=RiskGateResult(
                passed=False, gate="size", reasons=["Too many files modified"]
            )
        ),
    )

    with pytest.raises(typer.Exit) as exc:
        _execute(run_id, workspace=env["workspace_path"])
    assert exc.value.exit_code == 1

    _assert_target_unchanged(target, before)
    _assert_failure_evidence(wm, run_id)


# ── Scenario 7: Validation fails (patch doesn't apply) ──────────────────────


def test_validation_fails(env, monkeypatch: pytest.MonkeyPatch) -> None:
    wm, run_id = env["wm"], env["run_id"]
    target = env["target"]
    before = _snapshot_target(target)

    diff_text = "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-hello\n+world"
    output = ExecutorOutput(
        applied=[
            FileChange(task_id="T1", file="file.txt", status=TaskStatus.APPLIED, diff=diff_text)
        ],
    )

    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)
    monkeypatch.setattr(
        "orchestrator.agents.executor.run",
        MagicMock(return_value=(output, {"cost_usd": 0.01})),
    )
    monkeypatch.setattr(
        "orchestrator.risk.check_patch_gate",
        MagicMock(return_value=RiskGateResult(passed=True, gate="size", reasons=[])),
    )

    monkeypatch.setattr(
        "orchestrator.validation_workspace.create_validation_workspace",
        MagicMock(return_value=_mock_validation_workspace()),
    )
    monkeypatch.setattr(
        "orchestrator.validation_workspace.apply_patch_to_copy",
        MagicMock(
            return_value=GitCommandResult(return_code=1, stdout="", stderr="patch does not apply")
        ),
    )

    _execute(run_id, workspace=env["workspace_path"])

    _assert_target_unchanged(target, before)

    run_dir = wm.run_dir(run_id)
    run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_json["status"] == "validation_failed"
    assert (run_dir / "patch.diff").exists()
    assert (run_dir / "validation.json").exists()

    val = json.loads((run_dir / "validation.json").read_text(encoding="utf-8"))
    assert val["overall_passed"] is False


# ── D-011d Part 2: executor-side provider fallback warning ─────────────────


def _read_pipeline_events(logs_dir: Path) -> list[dict]:
    jsonl = logs_dir / "pipeline.jsonl"
    if not jsonl.exists():
        return []
    return [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line]


def test_fallback_prints_warning_and_emits_event(
    env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    run_id = env["run_id"]

    diff_text = "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-hello\n+world"
    output = ExecutorOutput(
        applied=[
            FileChange(
                task_id="T1",
                file="file.txt",
                status=TaskStatus.APPLIED,
                diff=diff_text,
                provider_name="openrouter",
                primary_provider_attempted="gemini",
                primary_failure_category="credit_exhausted",
            )
        ],
    )

    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)
    monkeypatch.setattr(
        "orchestrator.agents.executor.run",
        MagicMock(return_value=(output, {"cost_usd": 0.01})),
    )
    monkeypatch.setattr(
        "orchestrator.risk.check_patch_gate",
        MagicMock(return_value=RiskGateResult(passed=False, gate="size", reasons=["blocked"])),
    )

    with pytest.raises(typer.Exit):
        _execute(run_id, workspace=env["workspace_path"])

    captured = capsys.readouterr()
    assert "Fallback" in captured.out
    assert "gemini" in captured.out
    assert "openrouter" in captured.out
    assert "credit_exhausted" in captured.out

    logs_dir = env["workspace_path"] / "logs"
    events = _read_pipeline_events(logs_dir)
    fallback_events = [e for e in events if e.get("event") == "provider_fallback"]
    assert len(fallback_events) == 1
    ev = fallback_events[0]
    assert ev["stage"] == "executor"
    assert ev["data"]["primary_provider"] == "gemini"
    assert ev["data"]["used_provider"] == "openrouter"
    assert ev["data"]["category"] == "credit_exhausted"


def test_no_fallback_does_not_print_warning_or_emit_event(
    env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    run_id = env["run_id"]

    diff_text = "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-hello\n+world"
    output = ExecutorOutput(
        applied=[
            FileChange(
                task_id="T1",
                file="file.txt",
                status=TaskStatus.APPLIED,
                diff=diff_text,
                provider_name="gemini",
                primary_provider_attempted="gemini",
                primary_failure_category=None,
            )
        ],
    )

    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)
    monkeypatch.setattr(
        "orchestrator.agents.executor.run",
        MagicMock(return_value=(output, {"cost_usd": 0.01})),
    )
    monkeypatch.setattr(
        "orchestrator.risk.check_patch_gate",
        MagicMock(return_value=RiskGateResult(passed=False, gate="size", reasons=["blocked"])),
    )

    with pytest.raises(typer.Exit):
        _execute(run_id, workspace=env["workspace_path"])

    captured = capsys.readouterr()
    assert "Fallback" not in captured.out

    logs_dir = env["workspace_path"] / "logs"
    events = _read_pipeline_events(logs_dir)
    fallback_events = [e for e in events if e.get("event") == "provider_fallback"]
    assert fallback_events == []


def test_syntax_error_after_fallback_excluded_from_warning(
    env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A fallback provider's response that failed syntax validation is a
    terminal failure — it must appear in the error panel but never in the
    yellow fallback warning."""
    run_id = env["run_id"]

    output = ExecutorOutput(
        errors=[
            FileChange(
                task_id="T1",
                file="file.txt",
                status=TaskStatus.ERROR,
                error="not valid Python: line 3",
                provider_name="openrouter",
                primary_provider_attempted="gemini",
                primary_failure_category="rate_limited",
            )
        ],
    )

    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)
    monkeypatch.setattr(
        "orchestrator.agents.executor.run",
        MagicMock(return_value=(output, {"cost_usd": 0.01})),
    )

    with pytest.raises(typer.Exit):
        _execute(run_id, workspace=env["workspace_path"])

    captured = capsys.readouterr()
    assert "Fallback" not in captured.out
    assert "Executor Errors" in captured.out

    logs_dir = env["workspace_path"] / "logs"
    events = _read_pipeline_events(logs_dir)
    fallback_events = [e for e in events if e.get("event") == "provider_fallback"]
    assert fallback_events == []


def test_fallback_warning_escapes_rich_markup(
    env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A category, provider, or file path value containing literal brackets
    must not raise MarkupError or corrupt the printed line."""
    run_id = env["run_id"]

    diff_text = "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-hello\n+world"
    output = ExecutorOutput(
        applied=[
            FileChange(
                task_id="T1",
                file="weird[1].txt",
                status=TaskStatus.APPLIED,
                diff=diff_text,
                provider_name="openrouter",
                primary_provider_attempted="gemini",
                primary_failure_category="cred[it]_exhausted",
            )
        ],
    )

    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)
    monkeypatch.setattr(
        "orchestrator.agents.executor.run",
        MagicMock(return_value=(output, {"cost_usd": 0.01})),
    )
    monkeypatch.setattr(
        "orchestrator.risk.check_patch_gate",
        MagicMock(return_value=RiskGateResult(passed=False, gate="size", reasons=["blocked"])),
    )

    exc_info = None
    try:
        _execute(run_id, workspace=env["workspace_path"])
    except typer.Exit:
        pass
    except Exception as exc:  # noqa: BLE001 — assert no other exception type leaked
        exc_info = exc
    assert exc_info is None


# ── Scenario 8: Happy path ──────────────────────────────────────────────────


def test_happy_path(env, monkeypatch: pytest.MonkeyPatch) -> None:
    wm, run_id = env["wm"], env["run_id"]
    target = env["target"]
    before = _snapshot_target(target)

    diff_text = "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-hello\n+world"
    output = ExecutorOutput(
        applied=[
            FileChange(task_id="T1", file="file.txt", status=TaskStatus.APPLIED, diff=diff_text)
        ],
    )
    validator_result = ValidatorOutput(overall_passed=True, tools=[], run_id=run_id)

    monkeypatch.setattr("orchestrator.clients.bootstrap.bootstrap_environment", lambda **kw: None)
    monkeypatch.setattr(
        "orchestrator.agents.executor.run",
        MagicMock(return_value=(output, {"cost_usd": 0.01})),
    )
    monkeypatch.setattr(
        "orchestrator.risk.check_patch_gate",
        MagicMock(return_value=RiskGateResult(passed=True, gate="size", reasons=[])),
    )
    monkeypatch.setattr(
        "orchestrator.validation_workspace.create_validation_workspace",
        MagicMock(return_value=_mock_validation_workspace()),
    )
    monkeypatch.setattr(
        "orchestrator.validation_workspace.apply_patch_to_copy",
        MagicMock(return_value=GitCommandResult(return_code=0, stdout="Applied", stderr="")),
    )
    monkeypatch.setattr(
        "orchestrator.validation_workspace.run_validation_in_copy",
        MagicMock(return_value=validator_result),
    )

    _execute(run_id, workspace=env["workspace_path"])

    _assert_target_unchanged(target, before)
    _assert_success_evidence(wm, run_id)

    run_dir = wm.run_dir(run_id)
    run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_json["patch_checksum"] is not None
    assert len(run_json["patch_checksum"]) == 64
