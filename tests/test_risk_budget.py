from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from orchestrator.main import app
from orchestrator.risk import (
    _count_diff_files,
    _count_diff_lines,
    check_patch_gate,
    check_plan_gate,
)
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.schemas.executor_output import ExecutorOutput, FileChange
from orchestrator.schemas.findings import PyProjectInfo, ScanFindings, TestSuiteInfo, ToolInfo
from orchestrator.schemas.scout_output import ScoutOutput
from orchestrator.schemas.validator_output import ToolResult, ValidatorOutput
from orchestrator.workspace import WorkspaceManager

runner = CliRunner()

# ── Helpers ──────────────────────────────────────────────────────────────


def _run_meta(
    risk_budget: str = "low",
    max_files: int = 2,
    max_diff_lines: int = 100,
) -> RunMetadata:
    return RunMetadata(
        run_id="test",
        target_path="/dummy",
        workspace_path="/dummy",
        base_commit="a" * 40,
        branch="main",
        v1_supported=True,
        risk_budget=risk_budget,
        max_files=max_files,
        max_diff_lines=max_diff_lines,
    )


def _arch_output(tasks: list[Task]) -> ArchitectOutput:
    return ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=tasks,
        blockers=[],
    )


# ── Unit: check_plan_gate ───────────────────────────────────────────────


class TestCheckPlanGate:
    def test_low_budget_all_low_passes(self):
        meta = _run_meta(risk_budget="low")
        arch = _arch_output(
            [
                Task(
                    task_id="T1",
                    title="t1",
                    description="",
                    files_to_modify=["a.py"],
                    priority="high",
                    effort="low",
                    risk_level="low",
                    dependencies=[],
                ),
            ]
        )
        result = check_plan_gate(meta, arch)
        assert result.passed is True
        assert result.gate == "plan"
        assert result.reasons == []

    def test_low_budget_medium_task_blocked(self):
        meta = _run_meta(risk_budget="low")
        arch = _arch_output(
            [
                Task(
                    task_id="T1",
                    title="t1",
                    description="",
                    files_to_modify=["a.py"],
                    priority="high",
                    effort="low",
                    risk_level="medium",
                    dependencies=[],
                ),
            ]
        )
        result = check_plan_gate(meta, arch)
        assert result.passed is False
        assert any("medium-risk" in r for r in result.reasons)

    def test_any_budget_high_task_blocked(self):
        for budget in ["low", "medium"]:
            meta = _run_meta(risk_budget=budget)
            arch = _arch_output(
                [
                    Task(
                        task_id="T1",
                        title="t1",
                        description="",
                        files_to_modify=["a.py"],
                        priority="high",
                        effort="low",
                        risk_level="high",
                        dependencies=[],
                    ),
                ]
            )
            result = check_plan_gate(meta, arch)
            assert result.passed is False
            assert any("High-risk tasks are not applicable in V1" in r for r in result.reasons)

    def test_medium_budget_medium_task_passes(self):
        meta = _run_meta(risk_budget="medium")
        arch = _arch_output(
            [
                Task(
                    task_id="T1",
                    title="t1",
                    description="",
                    files_to_modify=["a.py"],
                    priority="high",
                    effort="low",
                    risk_level="medium",
                    dependencies=[],
                ),
            ]
        )
        result = check_plan_gate(meta, arch)
        assert result.passed is True

    def test_too_many_files_blocked(self):
        meta = _run_meta(max_files=1)
        arch = _arch_output(
            [
                Task(
                    task_id="T1",
                    title="t1",
                    description="",
                    files_to_modify=["a.py"],
                    priority="high",
                    effort="low",
                    risk_level="low",
                    dependencies=[],
                ),
                Task(
                    task_id="T2",
                    title="t2",
                    description="",
                    files_to_modify=["b.py"],
                    priority="high",
                    effort="low",
                    risk_level="low",
                    dependencies=[],
                ),
            ]
        )
        result = check_plan_gate(meta, arch)
        assert result.passed is False
        assert any("exceeding max_files" in r for r in result.reasons)

    def test_file_count_within_limit_passes(self):
        meta = _run_meta(max_files=2)
        arch = _arch_output(
            [
                Task(
                    task_id="T1",
                    title="t1",
                    description="",
                    files_to_modify=["a.py"],
                    priority="high",
                    effort="low",
                    risk_level="low",
                    dependencies=[],
                ),
            ]
        )
        result = check_plan_gate(meta, arch)
        assert result.passed is True

    def test_multiple_violations_collected(self):
        meta = _run_meta(risk_budget="low", max_files=1)
        arch = _arch_output(
            [
                Task(
                    task_id="T1",
                    title="t1",
                    description="",
                    files_to_modify=["a.py"],
                    priority="high",
                    effort="low",
                    risk_level="high",
                    dependencies=[],
                ),
                Task(
                    task_id="T2",
                    title="t2",
                    description="",
                    files_to_modify=["b.py"],
                    priority="high",
                    effort="low",
                    risk_level="low",
                    dependencies=[],
                ),
            ]
        )
        result = check_plan_gate(meta, arch)
        assert result.passed is False
        assert len(result.reasons) >= 2

    def test_empty_plan_passes(self):
        meta = _run_meta()
        arch = _arch_output([])
        result = check_plan_gate(meta, arch)
        assert result.passed is True


# ── Unit: diff counting helpers ─────────────────────────────────────────


class TestCountDiffHelpers:
    def test_count_diff_lines_normal(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        assert _count_diff_lines(diff) == 2

    def test_count_diff_lines_new_file(self):
        diff = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+line1\n"
            "+line2\n"
            "+line3\n"
        )
        assert _count_diff_lines(diff) == 3

    def test_count_diff_lines_deleted_file(self):
        diff = (
            "diff --git a/old.py b/old.py\n"
            "deleted file mode 100644\n"
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "@@ -1,3 +0,0 @@\n"
            "-gone1\n"
            "-gone2\n"
        )
        assert _count_diff_lines(diff) == 2

    def test_count_diff_files(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -5 +5 @@\n"
            "-x\n"
            "+y\n"
        )
        assert _count_diff_files(diff) == 2

    def test_count_diff_files_new_file(self):
        diff = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1 @@\n"
            "+hello\n"
        )
        assert _count_diff_files(diff) == 1

    def test_empty_diff(self):
        assert _count_diff_lines("") == 0
        assert _count_diff_files("") == 0

    def test_ignore_index_line(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "index abc123..def456 100644\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        assert _count_diff_lines(diff) == 2

    def test_no_diff_lines_counts_zero(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n"
        assert _count_diff_lines(diff) == 0

    def test_multi_hunk_accumulates(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
            "@@ -10 +10 @@\n"
            "-c\n"
            "+d\n"
        )
        assert _count_diff_lines(diff) == 4


# ── Unit: check_patch_gate ──────────────────────────────────────────────


class TestCheckPatchGate:
    def test_within_limits_passes(self):
        meta = _run_meta(max_files=2, max_diff_lines=100)
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        result = check_patch_gate(meta, diff)
        assert result.passed is True
        assert result.gate == "patch"

    def test_exceeds_files_blocked(self):
        meta = _run_meta(max_files=1, max_diff_lines=100)
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1 +1 @@\n"
            "-x\n"
            "+y\n"
        )
        result = check_patch_gate(meta, diff)
        assert result.passed is False
        assert any("exceeding max_files" in r for r in result.reasons)

    def test_exceeds_diff_lines_blocked(self):
        meta = _run_meta(max_files=5, max_diff_lines=2)
        diff_lines = "\n".join(
            [
                "diff --git a/a.py b/a.py",
                "--- a/a.py",
                "+++ b/a.py",
                "@@ -1 +1 @@",
                "-a",
                "+b",
                "+c",
            ]
        )
        result = check_patch_gate(meta, diff_lines)
        assert result.passed is False
        assert any("exceeding max_diff_lines" in r for r in result.reasons)

    def test_multiple_violations_collected(self):
        meta = _run_meta(max_files=1, max_diff_lines=1)
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ a/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1 +1 @@\n"
            "-x\n"
            "+y\n"
        )
        result = check_patch_gate(meta, diff)
        assert result.passed is False
        assert len(result.reasons) >= 2

    def test_empty_diff_passes(self):
        meta = _run_meta()
        result = check_patch_gate(meta, "")
        assert result.passed is True


# ── Integration: CLI gate enforcement ───────────────────────────────────


def _init_git_repo(path: Path) -> None:
    import subprocess

    kw = {"cwd": path, "check": True, "capture_output": True}
    subprocess.run(["git", "init"], **kw)
    subprocess.run(["git", "config", "user.name", "Test User"], **kw)
    subprocess.run(["git", "config", "user.email", "test@example.com"], **kw)
    (path / "README.md").write_text("Hello\n")
    subprocess.run(["git", "add", "README.md"], **kw)
    subprocess.run(["git", "commit", "-m", "initial commit"], **kw)


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


def _mock_scan_findings() -> ScanFindings:
    return ScanFindings(
        repository_root="/tmp",
        base_commit="abc123",
        branch="main",
        v1_supported=True,
        support_reasons=["pyproject", "ruff", "pytest", "tests"],
        unsupported_reasons=[],
        pyproject=PyProjectInfo(exists=True, valid=True, build_backend="hatchling.build"),
        ruff=ToolInfo(available=True, version="0.9.0"),
        pytest=ToolInfo(available=True, version="8.3.0"),
        test_suite=TestSuiteInfo(detected=True, type="tests_dir"),
        total_python_files=0,
        packages=[],
        modules=[],
        hotspots=[],
    )


def _overwrite_findings_with_scout_output(workspace_dir: Path, scout_out: ScoutOutput) -> None:
    """Overwrite findings.json with ScoutOutput format for plan compatibility."""
    ws = WorkspaceManager(workspace_dir)
    runs_dir = workspace_dir / "runs"
    run_id = list(runs_dir.iterdir())[0].name
    ws.write_artifact(run_id, "findings.json", scout_out.model_dump_json(indent=2))


def _run_scan(target_repo: Path, workspace_dir: Path) -> str:
    """Run scan with mock agents and return run_id."""
    scan_findings = _mock_scan_findings()
    with patch("orchestrator.commands.scan.scan", return_value=scan_findings):
        scan_res = runner.invoke(app, ["scan", str(target_repo), "--workspace", str(workspace_dir)])
    assert scan_res.exit_code == 0
    runs_dir = workspace_dir / "runs"
    return list(runs_dir.iterdir())[0].name


def _run_plan(run_id: str, workspace_dir: Path):
    """Run plan and return the result."""
    return runner.invoke(app, ["plan", run_id, "--workspace", str(workspace_dir)])


def _mock_arch(tasks: list[Task]) -> ArchitectOutput:
    return ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=tasks,
        blockers=[],
    )


def _mock_scout() -> tuple[ScoutOutput, dict]:
    return (
        ScoutOutput(hotspots=[], recommended_order=[], risks=[], summary="Test"),
        {"latency_ms": 100, "cost_usd": 0.01},
    )


class TestPlanGateIntegration:
    def test_plan_blocked_by_high_risk_task(self, target_repo: Path, workspace_dir: Path):
        scout_out, _ = _mock_scout()
        arch_meta = {"latency_ms": 200, "cost_usd": 0.02}
        arch_out = _mock_arch(
            [
                Task(
                    task_id="T1",
                    title="Risk task",
                    description="",
                    files_to_modify=["README.md"],
                    priority="high",
                    effort="low",
                    risk_level="high",
                    dependencies=[],
                ),
            ]
        )

        with patch("orchestrator.agents.architect.run", return_value=(arch_out, arch_meta)):
            run_id = _run_scan(target_repo, workspace_dir)
            _overwrite_findings_with_scout_output(workspace_dir, scout_out)
            plan_res = _run_plan(run_id, workspace_dir)

        assert plan_res.exit_code == 1
        assert "high-risk" in plan_res.stdout

    def test_plan_blocked_by_too_many_files(self, target_repo: Path, workspace_dir: Path):
        scout_out, _ = _mock_scout()
        arch_meta = {"latency_ms": 200, "cost_usd": 0.02}
        arch_out = _mock_arch(
            [
                Task(
                    task_id="T1",
                    title="Task A",
                    description="",
                    files_to_modify=["README.md", "a.py", "b.py"],
                    priority="high",
                    effort="low",
                    risk_level="low",
                    dependencies=[],
                ),
            ]
        )

        with patch("orchestrator.agents.architect.run", return_value=(arch_out, arch_meta)):
            run_id = _run_scan(target_repo, workspace_dir)
            _overwrite_findings_with_scout_output(workspace_dir, scout_out)
            plan_res = _run_plan(run_id, workspace_dir)

        assert plan_res.exit_code == 1
        assert "exceeding max_files" in plan_res.stdout

    def test_plan_passes_with_compliant_task(self, target_repo: Path, workspace_dir: Path):
        scout_out, _ = _mock_scout()
        arch_meta = {"latency_ms": 200, "cost_usd": 0.02}
        arch_out = _mock_arch(
            [
                Task(
                    task_id="T1",
                    title="Safe task",
                    description="",
                    files_to_modify=["README.md"],
                    priority="high",
                    effort="low",
                    risk_level="low",
                    dependencies=[],
                ),
            ]
        )

        with patch("orchestrator.agents.architect.run", return_value=(arch_out, arch_meta)):
            run_id = _run_scan(target_repo, workspace_dir)
            _overwrite_findings_with_scout_output(workspace_dir, scout_out)
            plan_res = _run_plan(run_id, workspace_dir)

        assert plan_res.exit_code == 0
        assert "Plan generated successfully!" in plan_res.stdout


def _mock_val(passed: bool = True) -> ValidatorOutput:
    return ValidatorOutput(
        overall_passed=passed,
        tools=[ToolResult(tool="ruff", passed=True, return_code=0)],
        llm_summary=None,
    )


class TestPatchGateIntegration:
    def _run_pipeline(
        self,
        target_repo: Path,
        workspace_dir: Path,
        arch_tasks: list[Task],
        exec_out: ExecutorOutput,
    ):
        scout_out, _ = _mock_scout()
        arch_meta = {"latency_ms": 200, "cost_usd": 0.02}
        exec_meta = {"latency_ms": 300, "cost_usd": 0.03}
        arch_out = _mock_arch(arch_tasks)
        val_out = _mock_val()

        with (
            patch("orchestrator.agents.architect.run", return_value=(arch_out, arch_meta)),
            patch("orchestrator.agents.executor.run", return_value=(exec_out, exec_meta)),
            patch(
                "orchestrator.validation_workspace.run_validation_in_copy",
                return_value=val_out,
            ),
        ):
            run_id = _run_scan(target_repo, workspace_dir)
            _overwrite_findings_with_scout_output(workspace_dir, scout_out)
            plan_res = _run_plan(run_id, workspace_dir)
            assert plan_res.exit_code == 0
            preview_res = runner.invoke(app, ["preview", run_id, "--workspace", str(workspace_dir)])
        return run_id, preview_res

    def test_preview_blocked_by_oversized_patch(self, target_repo: Path, workspace_dir: Path):
        big_diff_lines = [f"+line_{i}" for i in range(105)]
        big_diff = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1,105 @@\n"
            "-Hello\n" + "\n".join(big_diff_lines)
        )
        exec_out = ExecutorOutput(
            applied=[
                FileChange(
                    task_id="T1",
                    file="README.md",
                    status="applied",
                    diff=big_diff,
                )
            ],
        )

        _, preview_res = self._run_pipeline(
            target_repo,
            workspace_dir,
            arch_tasks=[
                Task(
                    task_id="T1",
                    title="Safe task",
                    description="",
                    files_to_modify=["README.md"],
                    priority="high",
                    effort="low",
                    risk_level="low",
                    dependencies=[],
                )
            ],
            exec_out=exec_out,
        )

        assert preview_res.exit_code == 1
        assert "exceeding max_diff_lines" in preview_res.stdout

    def test_preview_blocked_by_too_many_files_in_diff(
        self, target_repo: Path, workspace_dir: Path
    ):
        diff_3_files = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-Hello\n"
            "+Hello World\n"
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-x\n"
            "+y\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
        )
        exec_out = ExecutorOutput(
            applied=[
                FileChange(
                    task_id="T1",
                    file="README.md",
                    status="applied",
                    diff=diff_3_files,
                )
            ],
        )

        # Plan says 2 files (within max_files=2), but diff touches 3
        _, preview_res = self._run_pipeline(
            target_repo,
            workspace_dir,
            arch_tasks=[
                Task(
                    task_id="T1",
                    title="Task",
                    description="",
                    files_to_modify=["README.md", "a.py"],
                    priority="high",
                    effort="low",
                    risk_level="low",
                    dependencies=[],
                )
            ],
            exec_out=exec_out,
        )

        assert preview_res.exit_code == 1
        assert "exceeding max_files" in preview_res.stdout
