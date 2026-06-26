import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from orchestrator.git import GitCommandResult, current_head, resolve_git_root
from orchestrator.main import app
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.artifacts import PatchLifecycleState
from orchestrator.schemas.executor_output import ExecutorOutput, FileChange
from orchestrator.schemas.findings import (
    PyProjectInfo,
    ScanFindings,
    TestSuiteInfo,
    ToolInfo,
)
from orchestrator.schemas.scout_output import ScoutOutput
from orchestrator.schemas.validator_output import ToolResult, ValidatorOutput
from orchestrator.workspace import WorkspaceManager

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

    # Build a ScanFindings mock that reads the real git HEAD so apply can verify it later.
    def _mock_scan(target_path, ignore_dirs=None):
        root = resolve_git_root(target_path)
        head = current_head(root)
        branch_name = "main"
        return ScanFindings(
            repository_root=str(root),
            base_commit=head,
            branch=branch_name,
            v1_supported=True,
            support_reasons=["mocked"],
            unsupported_reasons=[],
            pyproject=PyProjectInfo(exists=True, valid=True, build_backend="hatchling.build"),
            ruff=ToolInfo(available=True, version="ruff 0.4.0"),
            pytest=ToolInfo(available=True, version="pytest 8.0.0"),
            test_suite=TestSuiteInfo(detected=True, type="tests_dir"),
            total_python_files=0,
            packages=[],
            modules=[],
            hotspots=[],
        )

    with (
        patch("orchestrator.commands.scan.scan", side_effect=_mock_scan),
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
        assert scan_res.exit_code == 0, scan_res.output
        assert "Scanner completed successfully!" in scan_res.stdout

        # Extract Run ID from output
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
        # V1 findings: check v1_supported field (no 'summary' in ScanFindings)
        assert findings_data["v1_supported"] is True

        # Overwrite findings.json with ScoutOutput so plan/preview/apply can proceed
        ws_mgr = WorkspaceManager(workspace_dir)
        ws_mgr.write_artifact(run_id, "findings.json", mock_scout_out.model_dump_json(indent=2))

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

    def _mock_scan(target_path, ignore_dirs=None):
        root = resolve_git_root(target_path)
        head = current_head(root)
        return ScanFindings(
            repository_root=str(root),
            base_commit=head,
            branch="main",
            v1_supported=True,
            support_reasons=["mocked"],
            unsupported_reasons=[],
            pyproject=PyProjectInfo(exists=True, valid=True, build_backend="hatchling.build"),
            ruff=ToolInfo(available=True, version="ruff 0.4.0"),
            pytest=ToolInfo(available=True, version="pytest 8.0.0"),
            test_suite=TestSuiteInfo(detected=True, type="tests_dir"),
            total_python_files=0,
            packages=[],
            modules=[],
            hotspots=[],
        )

    with (
        patch("orchestrator.commands.scan.scan", side_effect=_mock_scan),
        patch("orchestrator.agents.architect.run", return_value=(mock_arch_out, mock_arch_meta)),
        patch("orchestrator.agents.executor.run", return_value=(mock_exec_out, mock_exec_meta)),
        patch(
            "orchestrator.validation_workspace.run_validation_in_copy", return_value=mock_val_out
        ),
        patch("orchestrator.agents.validator.run", return_value=(mock_val_out, {})),
    ):
        scan_res = runner.invoke(app, ["scan", str(target_repo), "--workspace", str(workspace_dir)])
        assert scan_res.exit_code == 0, scan_res.output

        runs_dir = workspace_dir / "runs"
        run_id = list(runs_dir.iterdir())[0].name

        # Overwrite findings.json with ScoutOutput so plan can proceed
        ws_mgr = WorkspaceManager(workspace_dir)
        ws_mgr.write_artifact(run_id, "findings.json", mock_scout_out.model_dump_json(indent=2))

        plan_res = runner.invoke(app, ["plan", run_id, "--workspace", str(workspace_dir)])
        assert plan_res.exit_code == 0, plan_res.output

        preview_res = runner.invoke(app, ["preview", run_id, "--workspace", str(workspace_dir)])
        assert preview_res.exit_code == 0, preview_res.output

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
    assert apply_data["rolled_back"] is True
    assert apply_data["rollback_head"] is not None
    assert apply_data["pre_apply_head"] is not None
    assert apply_data["pre_apply_branch"] is not None
    assert "apply failed" in apply_data["error"]

    run_json_path = workspace_dir / "runs" / run_id / "run.json"
    run_data = json.loads(run_json_path.read_text())
    assert run_data["status"] == "failed"
    assert run_data["apply_status"] == "rolled_back"
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


def test_rebaseable_blocks_apply(target_repo: Path, workspace_dir: Path):
    """REBASEABLE lifecycle state blocks the apply command in V1."""
    run_id = _prepare_run(target_repo, workspace_dir, runner)

    with patch(
        "orchestrator.lifecycle.classify_lifecycle",
        return_value=PatchLifecycleState.REBASEABLE,
    ):
        apply_res = runner.invoke(app, ["apply", run_id, "--workspace", str(workspace_dir)])

    assert apply_res.exit_code == 1
    assert "REBASEABLE" in apply_res.stdout

    # Verify lifecycle_state is persisted in run.json before exit
    run_json_path = workspace_dir / "runs" / run_id / "run.json"
    run_data = json.loads(run_json_path.read_text())
    assert run_data["lifecycle_state"] == "REBASEABLE"

    # Verify target was not modified
    assert (target_repo / "README.md").read_text() == "Hello\n"


# ---------------------------------------------------------------------------
# --issue-file integration tests
# ---------------------------------------------------------------------------


def test_v1_issue_file_flow(target_repo: Path, workspace_dir: Path, tmp_path: Path):
    """Full pipeline with --issue-file: scan -> plan --issue-file -> preview -> apply."""
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
+Hello World Issue
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

    issue_path = tmp_path / "test-issue.md"
    issue_content = (
        "---\ntitle: Fix README title\nseverity: low\nlabels: docs\n---\n"
        "The README should say Hello World Issue."
    )
    issue_path.write_text(issue_content, encoding="utf-8")

    def _mock_scan(target_path, ignore_dirs=None):
        root = resolve_git_root(target_path)
        head = current_head(root)
        return ScanFindings(
            repository_root=str(root),
            base_commit=head,
            branch="main",
            v1_supported=True,
            support_reasons=["mocked"],
            unsupported_reasons=[],
            pyproject=PyProjectInfo(exists=True, valid=True, build_backend="hatchling.build"),
            ruff=ToolInfo(available=True, version="ruff 0.4.0"),
            pytest=ToolInfo(available=True, version="pytest 8.0.0"),
            test_suite=TestSuiteInfo(detected=True, type="tests_dir"),
            total_python_files=0,
            packages=[],
            modules=[],
            hotspots=[],
        )

    with (
        patch("orchestrator.commands.scan.scan", side_effect=_mock_scan),
        patch(
            "orchestrator.agents.architect.run_from_issue",
            return_value=(mock_arch_out, mock_arch_meta),
        ),
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

        plan_res = runner.invoke(
            app,
            [
                "plan",
                run_id,
                "--workspace",
                str(workspace_dir),
                "--issue-file",
                str(issue_path),
            ],
        )
        assert plan_res.exit_code == 0, plan_res.stdout
        assert "Plan generated successfully!" in plan_res.stdout

        issue_md_path = runs_dir / run_id / "issue.md"
        assert issue_md_path.exists()
        assert issue_md_path.read_text(encoding="utf-8") == issue_content

        plan_json_path = runs_dir / run_id / "plan.json"
        assert plan_json_path.exists()
        plan_data = json.loads(plan_json_path.read_text())
        assert len(plan_data["implementation_plan"]) == 1

        run_data = json.loads((runs_dir / run_id / "run.json").read_text())
        assert run_data["status"] == "planned"
        assert run_data["goal"] == "Fix README title"
        assert run_data["affected_files"] == ["README.md"]

        preview_res = runner.invoke(app, ["preview", run_id, "--workspace", str(workspace_dir)])
        assert preview_res.exit_code == 0, preview_res.stdout
        assert "Preview and validation completed successfully!" in preview_res.stdout
        patch_diff_path = runs_dir / run_id / "patch.diff"
        assert patch_diff_path.exists()

        apply_res = runner.invoke(app, ["apply", run_id, "--workspace", str(workspace_dir)])
        assert apply_res.exit_code == 0, apply_res.stdout
        assert "Patch applied successfully" in apply_res.stdout
        assert (target_repo / "README.md").read_text() == "Hello World Issue\n"


def test_plan_with_issue_file_not_found(target_repo: Path, workspace_dir: Path, tmp_path: Path):
    """--issue-file pointing to nonexistent path exits with code 1."""

    def _mock_scan(target_path, ignore_dirs=None):
        root = resolve_git_root(target_path)
        head = current_head(root)
        return ScanFindings(
            repository_root=str(root),
            base_commit=head,
            branch="main",
            v1_supported=True,
            support_reasons=["mocked"],
            unsupported_reasons=[],
            pyproject=PyProjectInfo(exists=True, valid=True, build_backend="hatchling.build"),
            ruff=ToolInfo(available=True, version="ruff 0.4.0"),
            pytest=ToolInfo(available=True, version="pytest 8.0.0"),
            test_suite=TestSuiteInfo(detected=True, type="tests_dir"),
            total_python_files=0,
            packages=[],
            modules=[],
            hotspots=[],
        )

    with patch("orchestrator.commands.scan.scan", side_effect=_mock_scan):
        scan_res = runner.invoke(app, ["scan", str(target_repo), "--workspace", str(workspace_dir)])
        assert scan_res.exit_code == 0

        runs_dir = workspace_dir / "runs"
        run_id = list(runs_dir.iterdir())[0].name

    missing_path = tmp_path / "does-not-exist.md"
    plan_res = runner.invoke(
        app,
        ["plan", run_id, "--workspace", str(workspace_dir), "--issue-file", str(missing_path)],
    )
    assert plan_res.exit_code == 1
    assert "Issue file not found" in plan_res.stdout


def test_plan_issue_file_overrides_findings(target_repo: Path, workspace_dir: Path, tmp_path: Path):
    """When findings.json exists and --issue-file is used, a warning is printed."""
    mock_arch_out = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[
            Task(
                task_id="T1",
                title="Fix README",
                description="Fix README",
                files_to_modify=["README.md"],
                priority="low",
                effort="low",
                risk_level="low",
            )
        ],
        blockers=[],
    )
    mock_arch_meta = {"latency_ms": 100, "cost_usd": 0.01}

    issue_path = tmp_path / "override.md"
    issue_path.write_text("---\ntitle: Override test\n---\nBody", encoding="utf-8")

    def _mock_scan(target_path, ignore_dirs=None):
        root = resolve_git_root(target_path)
        head = current_head(root)
        return ScanFindings(
            repository_root=str(root),
            base_commit=head,
            branch="main",
            v1_supported=True,
            support_reasons=["mocked"],
            unsupported_reasons=[],
            pyproject=PyProjectInfo(exists=True, valid=True, build_backend="hatchling.build"),
            ruff=ToolInfo(available=True, version="ruff 0.4.0"),
            pytest=ToolInfo(available=True, version="pytest 8.0.0"),
            test_suite=TestSuiteInfo(detected=True, type="tests_dir"),
            total_python_files=0,
            packages=[],
            modules=[],
            hotspots=[],
        )

    with (
        patch("orchestrator.commands.scan.scan", side_effect=_mock_scan),
        patch(
            "orchestrator.agents.architect.run_from_issue",
            return_value=(mock_arch_out, mock_arch_meta),
        ),
    ):
        scan_res = runner.invoke(app, ["scan", str(target_repo), "--workspace", str(workspace_dir)])
        assert scan_res.exit_code == 0

        runs_dir = workspace_dir / "runs"
        run_id = list(runs_dir.iterdir())[0].name

        plan_res = runner.invoke(
            app,
            ["plan", run_id, "--workspace", str(workspace_dir), "--issue-file", str(issue_path)],
        )
        assert plan_res.exit_code == 0, plan_res.stdout
        assert "takes precedence" in plan_res.stdout
        assert "Plan generated successfully!" in plan_res.stdout

        run_data = json.loads((runs_dir / run_id / "run.json").read_text())
        assert run_data["goal"] == "Override test"


# ---------------------------------------------------------------------------
# Fix #3 — --force-provider CLI tests
# ---------------------------------------------------------------------------


def test_preview_force_provider_invalid_exits_1():
    result = runner.invoke(app, ["preview", "fake-run", "--force-provider", "pepino"])
    assert result.exit_code == 1
    assert "Invalid value for --force-provider" in result.stdout


def test_preview_force_provider_valid_passes_to_executor(
    target_repo: Path, workspace_dir: Path
):
    mock_scout_out = ScoutOutput(
        hotspots=[],
        recommended_order=[],
        risks=[],
        summary="Test Scout Summary",
    )

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
                diff=(
                    "diff --git a/README.md b/README.md\n"
                    "--- a/README.md\n"
                    "+++ b/README.md\n"
                    "@@ -1 +1 @@\n"
                    "-Hello\n"
                    "+Hello Claude\n"
                ),
            )
        ]
    )
    mock_exec_meta = {"latency_ms": 300, "cost_usd": 0.03}

    mock_val_out = ValidatorOutput(
        overall_passed=True,
        tools=[ToolResult(tool="ruff", passed=True, return_code=0)],
        llm_summary=None,
    )

    def _mock_scan(target_path, ignore_dirs=None):
        root = resolve_git_root(target_path)
        head = current_head(root)
        return ScanFindings(
            repository_root=str(root),
            base_commit=head,
            branch="main",
            v1_supported=True,
            support_reasons=["mocked"],
            unsupported_reasons=[],
            pyproject=PyProjectInfo(exists=True, valid=True, build_backend="hatchling.build"),
            ruff=ToolInfo(available=True, version="ruff 0.4.0"),
            pytest=ToolInfo(available=True, version="pytest 8.0.0"),
            test_suite=TestSuiteInfo(detected=True, type="tests_dir"),
            total_python_files=0,
            packages=[],
            modules=[],
            hotspots=[],
        )

    captured_kwargs = {}

    def _mock_executor_run(**kwargs):
        captured_kwargs.update(kwargs)
        return (mock_exec_out, mock_exec_meta)

    with (
        patch("orchestrator.commands.scan.scan", side_effect=_mock_scan),
        patch("orchestrator.agents.architect.run", return_value=(mock_arch_out, mock_arch_meta)),
        patch("orchestrator.agents.executor.run", side_effect=_mock_executor_run),
        patch(
            "orchestrator.validation_workspace.run_validation_in_copy", return_value=mock_val_out
        ),
    ):
        scan_res = runner.invoke(app, ["scan", str(target_repo), "--workspace", str(workspace_dir)])
        assert scan_res.exit_code == 0

        runs_dir = workspace_dir / "runs"
        run_id = list(runs_dir.iterdir())[0].name

        ws_mgr = WorkspaceManager(workspace_dir)
        ws_mgr.write_artifact(run_id, "findings.json", mock_scout_out.model_dump_json(indent=2))

        plan_res = runner.invoke(app, ["plan", run_id, "--workspace", str(workspace_dir)])
        assert plan_res.exit_code == 0

        preview_res = runner.invoke(
            app,
            ["preview", run_id, "--workspace", str(workspace_dir), "--force-provider", "claude"],
        )
        assert preview_res.exit_code == 0, preview_res.stdout
        assert "Override activo" in preview_res.stdout
        assert captured_kwargs.get("force_provider") == "claude"


def test_preview_force_provider_cleans_staging(target_repo: Path, workspace_dir: Path):
    mock_scout_out = ScoutOutput(
        hotspots=[],
        recommended_order=[],
        risks=[],
        summary="Test Scout Summary",
    )

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
                diff="diff --git a/README.md b/README.md\n",
            )
        ]
    )
    mock_exec_meta = {"latency_ms": 300, "cost_usd": 0.03}

    mock_val_out = ValidatorOutput(
        overall_passed=True,
        tools=[ToolResult(tool="ruff", passed=True, return_code=0)],
        llm_summary=None,
    )

    def _mock_scan(target_path, ignore_dirs=None):
        root = resolve_git_root(target_path)
        head = current_head(root)
        return ScanFindings(
            repository_root=str(root),
            base_commit=head,
            branch="main",
            v1_supported=True,
            support_reasons=["mocked"],
            unsupported_reasons=[],
            pyproject=PyProjectInfo(exists=True, valid=True, build_backend="hatchling.build"),
            ruff=ToolInfo(available=True, version="ruff 0.4.0"),
            pytest=ToolInfo(available=True, version="pytest 8.0.0"),
            test_suite=TestSuiteInfo(detected=True, type="tests_dir"),
            total_python_files=0,
            packages=[],
            modules=[],
            hotspots=[],
        )

    with (
        patch("orchestrator.commands.scan.scan", side_effect=_mock_scan),
        patch("orchestrator.agents.architect.run", return_value=(mock_arch_out, mock_arch_meta)),
        patch("orchestrator.agents.executor.run", return_value=(mock_exec_out, mock_exec_meta)),
        patch(
            "orchestrator.validation_workspace.run_validation_in_copy", return_value=mock_val_out
        ),
    ):
        scan_res = runner.invoke(app, ["scan", str(target_repo), "--workspace", str(workspace_dir)])
        assert scan_res.exit_code == 0

        runs_dir = workspace_dir / "runs"
        run_id = list(runs_dir.iterdir())[0].name

        ws_mgr = WorkspaceManager(workspace_dir)
        ws_mgr.write_artifact(run_id, "findings.json", mock_scout_out.model_dump_json(indent=2))

        plan_res = runner.invoke(app, ["plan", run_id, "--workspace", str(workspace_dir)])
        assert plan_res.exit_code == 0

        staging_dir = runs_dir / run_id / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "leftover.py").write_text("old content")

        preview_res = runner.invoke(
            app,
            ["preview", run_id, "--workspace", str(workspace_dir), "--force-provider", "claude"],
        )
        assert preview_res.exit_code == 0, preview_res.stdout
        assert "se limpiaron 1 archivos previos" in preview_res.stdout
