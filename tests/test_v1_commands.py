import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from orchestrator.git import GitCommandResult
from orchestrator.main import app
from orchestrator.schemas.scout_output import ScoutOutput
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.executor_output import ExecutorOutput, FileChange
from orchestrator.schemas.validator_output import ValidatorOutput, ToolResult

runner = CliRunner()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("Hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"], cwd=path, check=True, capture_output=True
    )


@pytest.fixture
def target_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    return repo


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def test_v1_pipeline_flow(target_repo: Path, workspace_dir: Path):
    # Mock agents
    mock_scout_out = ScoutOutput(
        hotspots=[],
        recommended_order=[],
        risks=[],
        summary="Test Scout Summary",
    )
    mock_scout_meta = {"latency_ms": 100, "cost_usd": 0.01}

    mock_arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[
            Task(
                task_id="T1",
                title="Modify README",
                description="Modify README file",
                files_to_modify=["README.md"],
                priority="high",
                effort="low",
                risk_level="low",
            )
        ],
        blockers=[],
    )
    mock_arch_meta = {"latency_ms": 200, "cost_usd": 0.02}

    mock_exec_out = ExecutorOutput(
        applied=[
            FileChange(
                task_id="T1",
                file="README.md",
                status="applied",
                diff="""diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-Hello
+Hello World V1
""",
            )
        ]
    )
    mock_exec_meta = {"latency_ms": 300, "cost_usd": 0.03}

    mock_val_out = ValidatorOutput(
        overall_passed=True,
        tools=[ToolResult(tool="ruff", passed=True, return_code=0)],
        llm_summary=None,
    )

    with (
        patch("orchestrator.main.run_scout", return_value=(mock_scout_out, mock_scout_meta)),
        patch("orchestrator.agents.architect.run", return_value=(mock_arch_out, mock_arch_meta)),
        patch("orchestrator.agents.executor.run", return_value=(mock_exec_out, mock_exec_meta)),
        patch(
            "orchestrator.validation_workspace.run_validation_in_copy", return_value=mock_val_out
        ),
        patch("orchestrator.agents.validator.run", return_value=(mock_val_out, {})),
    ):
        # 1. SCAN
        scan_res = runner.invoke(
            app,
            ["scan", str(target_repo), "--workspace", str(workspace_dir)],
        )
        assert scan_res.exit_code == 0
        assert "Scout completed successfully!" in scan_res.stdout

        # Extract Run ID from output
        # Let's search runs/ directory
        runs_dir = workspace_dir / "runs"
        assert runs_dir.exists()
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1
        run_id = run_dirs[0].name

        # Verify initial run.json and findings.json
        run_json_path = runs_dir / run_id / "run.json"
        assert run_json_path.exists()
        run_data = json.loads(run_json_path.read_text())
        assert run_data["status"] == "scanned"
        assert run_data["v1_supported"] is True

        findings_json_path = runs_dir / run_id / "findings.json"
        assert findings_json_path.exists()
        findings_data = json.loads(findings_json_path.read_text())
        assert findings_data["summary"] == "Test Scout Summary"

        # 2. PLAN
        plan_res = runner.invoke(
            app,
            ["plan", run_id, "--workspace", str(workspace_dir)],
        )
        assert plan_res.exit_code == 0
        assert "Plan generated successfully!" in plan_res.stdout

        # Verify plan.json exists
        plan_json_path = runs_dir / run_id / "plan.json"
        assert plan_json_path.exists()
        plan_data = json.loads(plan_json_path.read_text())
        assert len(plan_data["implementation_plan"]) == 1

        run_data = json.loads(run_json_path.read_text())
        assert run_data["status"] == "planned"
        assert run_data["affected_files"] == ["README.md"]

        # 3. PREVIEW
        preview_res = runner.invoke(
            app,
            ["preview", run_id, "--workspace", str(workspace_dir)],
        )
        assert preview_res.exit_code == 0
        assert "Preview and validation completed successfully!" in preview_res.stdout

        patch_diff_path = runs_dir / run_id / "patch.diff"
        assert patch_diff_path.exists()
        assert "Hello World V1" in patch_diff_path.read_text()

        validation_json_path = runs_dir / run_id / "validation.json"
        assert validation_json_path.exists()
        val_data = json.loads(validation_json_path.read_text())
        assert val_data["overall_passed"] is True

        run_data = json.loads(run_json_path.read_text())
        assert run_data["status"] == "previewed"
        assert "patch_checksum" in run_data

        # 4. APPLY
        # Verify base_commit matches current head of target repo
        current_head_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target_repo,
            check=True,
            capture_output=True,
            text=True,
        )
        head_sha = current_head_res.stdout.strip()
        assert run_data["base_commit"] == head_sha

        apply_res = runner.invoke(
            app,
            ["apply", run_id, "--workspace", str(workspace_dir)],
        )
        assert apply_res.exit_code == 0
        assert "Patch applied successfully to branch" in apply_res.stdout

        # Verify new branch was created and file was modified
        branches_res = subprocess.run(
            ["git", "branch"], cwd=target_repo, check=True, capture_output=True, text=True
        )
        assert f"patchforge/{run_id}" in branches_res.stdout
        assert (target_repo / "README.md").read_text() == "Hello World V1\n"

        apply_json_path = runs_dir / run_id / "apply.json"
        assert apply_json_path.exists()
        apply_json_data = json.loads(apply_json_path.read_text())
        assert apply_json_data["success"] is True

        run_data = json.loads(run_json_path.read_text())
        assert run_data["status"] == "applied"


def _prepare_run(target_repo: Path, workspace_dir: Path, runner: CliRunner) -> str:
    """Helper: run scan → plan → preview with mocked agents, return run_id."""
    mock_scout_out = ScoutOutput(
        hotspots=[],
        recommended_order=[],
        risks=[],
        summary="Test Scout Summary",
    )
    mock_scout_meta = {"latency_ms": 100, "cost_usd": 0.01}

    mock_arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[
            Task(
                task_id="T1",
                title="Modify README",
                description="Modify README file",
                files_to_modify=["README.md"],
                priority="high",
                effort="low",
                risk_level="low",
            )
        ],
        blockers=[],
    )
    mock_arch_meta = {"latency_ms": 200, "cost_usd": 0.02}

    mock_exec_out = ExecutorOutput(
        applied=[
            FileChange(
                task_id="T1",
                file="README.md",
                status="applied",
                diff="""diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-Hello
+Hello World V1
""",
            )
        ]
    )
    mock_exec_meta = {"latency_ms": 300, "cost_usd": 0.03}

    mock_val_out = ValidatorOutput(
        overall_passed=True,
        tools=[ToolResult(tool="ruff", passed=True, return_code=0)],
        llm_summary=None,
    )

    with (
        patch("orchestrator.main.run_scout", return_value=(mock_scout_out, mock_scout_meta)),
        patch("orchestrator.agents.architect.run", return_value=(mock_arch_out, mock_arch_meta)),
        patch("orchestrator.agents.executor.run", return_value=(mock_exec_out, mock_exec_meta)),
        patch(
            "orchestrator.validation_workspace.run_validation_in_copy", return_value=mock_val_out
        ),
        patch("orchestrator.agents.validator.run", return_value=(mock_val_out, {})),
    ):
        scan_res = runner.invoke(app, ["scan", str(target_repo), "--workspace", str(workspace_dir)])
        assert scan_res.exit_code == 0

        runs_dir = workspace_dir / "runs"
        run_id = list(runs_dir.iterdir())[0].name

        plan_res = runner.invoke(app, ["plan", run_id, "--workspace", str(workspace_dir)])
        assert plan_res.exit_code == 0

        preview_res = runner.invoke(app, ["preview", run_id, "--workspace", str(workspace_dir)])
        assert preview_res.exit_code == 0

    return run_id


def test_apply_failure_triggers_force_reset(target_repo: Path, workspace_dir: Path):
    run_id = _prepare_run(target_repo, workspace_dir, runner)

    with patch(
        "orchestrator.git.apply_patch",
        return_value=GitCommandResult(return_code=1, stdout="", stderr="apply failed"),
    ):
        apply_res = runner.invoke(app, ["apply", run_id, "--workspace", str(workspace_dir)])

    assert apply_res.exit_code == 1

    apply_json_path = workspace_dir / "runs" / run_id / "apply.json"
    assert apply_json_path.exists()
    apply_data = json.loads(apply_json_path.read_text())
    assert apply_data["success"] is False
    assert apply_data["pre_apply_head"] is not None
    assert apply_data["pre_apply_branch"] is not None
    assert "apply failed" in apply_data["error"]

    run_json_path = workspace_dir / "runs" / run_id / "run.json"
    run_data = json.loads(run_json_path.read_text())
    assert run_data["status"] == "failed"
    assert run_data["apply_status"] == "failed"
    assert "apply.json" in run_data["failure_artifacts"]

    # Verify working tree is clean (rollback succeeded)
    assert (target_repo / "README.md").read_text() == "Hello\n"


def test_apply_failure_reset_failure_fatal(target_repo: Path, workspace_dir: Path):
    run_id = _prepare_run(target_repo, workspace_dir, runner)

    with (
        patch(
            "orchestrator.git.apply_patch",
            return_value=GitCommandResult(return_code=1, stdout="", stderr="apply failed"),
        ),
        patch(
            "orchestrator.git.force_reset_apply",
            return_value=GitCommandResult(return_code=1, stdout="", stderr="reset failed"),
        ),
    ):
        apply_res = runner.invoke(app, ["apply", run_id, "--workspace", str(workspace_dir)])

    assert apply_res.exit_code == 1
    assert "FATAL" in apply_res.stdout
    assert "reset failed" in apply_res.stdout

    apply_json_path = workspace_dir / "runs" / run_id / "apply.json"
    assert apply_json_path.exists()
    apply_data = json.loads(apply_json_path.read_text())
    assert apply_data["success"] is False
    assert apply_data["pre_apply_head"] is not None

    run_json_path = workspace_dir / "runs" / run_id / "run.json"
    run_data = json.loads(run_json_path.read_text())
    assert run_data["status"] == "failed"
    assert "apply.json" in run_data.get("failure_artifacts", [])


def test_post_apply_validation_failure_triggers_rollback(target_repo: Path, workspace_dir: Path):
    run_id = _prepare_run(target_repo, workspace_dir, runner)

    mock_failed_val = ValidatorOutput(
        overall_passed=False,
        tools=[ToolResult(tool="ruff", passed=False, return_code=1)],
        llm_summary="Linting failed",
    )

    with patch("orchestrator.agents.validator.run", return_value=(mock_failed_val, {})):
        apply_res = runner.invoke(app, ["apply", run_id, "--workspace", str(workspace_dir)])

    assert apply_res.exit_code == 1
    assert (
        "post-apply validation failed" in apply_res.stdout.lower()
        or "rolling back" in apply_res.stdout.lower()
    )

    apply_json_path = workspace_dir / "runs" / run_id / "apply.json"
    assert apply_json_path.exists()
    apply_data = json.loads(apply_json_path.read_text())
    assert apply_data["success"] is False
    assert apply_data["rolled_back"] is True
    assert apply_data["pre_apply_head"] is not None
    assert apply_data["rollback_head"] is not None

    run_json_path = workspace_dir / "runs" / run_id / "run.json"
    run_data = json.loads(run_json_path.read_text())
    assert run_data["status"] == "failed"
    assert run_data["apply_status"] == "rolled_back"
    assert "post_apply_failure.json" in run_data["failure_artifacts"]

    # Verify working tree is restored
    assert (target_repo / "README.md").read_text() == "Hello\n"


def test_patch_checksum_mismatch_blocks_apply(target_repo: Path, workspace_dir: Path):
    run_id = _prepare_run(target_repo, workspace_dir, runner)

    # Tamper with patch.diff to cause checksum mismatch
    patch_path = workspace_dir / "runs" / run_id / "patch.diff"
    original = patch_path.read_text()
    patch_path.write_text(original + "\n")

    apply_res = runner.invoke(app, ["apply", run_id, "--workspace", str(workspace_dir)])
    assert apply_res.exit_code == 1
    assert "checksum mismatch" in apply_res.stdout.lower()

    # Verify target was not modified
    assert (target_repo / "README.md").read_text() == "Hello\n"
