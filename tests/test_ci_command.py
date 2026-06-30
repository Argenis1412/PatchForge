"""Tests for the patchforge ci CLI command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from orchestrator.main import app
from orchestrator.schemas.ci_result import CiResult

runner = CliRunner()


def _init_repo(repo: Path) -> None:
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    (repo / "hello.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# CLI validation tests
# ---------------------------------------------------------------------------


def test_ci_rejects_invalid_risk_budget(tmp_path):
    result = runner.invoke(
        app,
        ["ci", str(tmp_path), "--workspace", str(tmp_path / "ws"), "--risk-budget", "extreme"],
    )
    assert result.exit_code == 1
    assert "risk-budget" in result.stdout.lower() or "risk-budget" in (result.stderr or "").lower()


def test_ci_rejects_negative_issue_number(tmp_path):
    result = runner.invoke(
        app,
        ["ci", str(tmp_path), "--workspace", str(tmp_path / "ws"), "--issue-number", "-1"],
    )
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# execute() unit tests (mocked agents)
# ---------------------------------------------------------------------------


def _make_scan_findings(*, v1_supported: bool = True):
    mock = MagicMock()
    mock.base_commit = "a" * 40
    mock.branch = "main"
    mock.v1_supported = v1_supported
    mock.support_reasons = []
    mock.unsupported_reasons = ["no tests"] if not v1_supported else []
    mock.hotspots = []
    mock.model_dump_json.return_value = json.dumps(
        {
            "base_commit": "a" * 40,
            "branch": "main",
            "v1_supported": v1_supported,
            "support_reasons": [],
            "unsupported_reasons": [],
            "hotspots": [],
            "summary": "test summary",
        }
    )
    return mock


def _make_arch_output():
    mock = MagicMock()
    mock.implementation_plan = []
    mock.model_dump_json.return_value = json.dumps(
        {
            "implementation_plan": [],
        }
    )
    return mock


def _make_executor_output():
    mock = MagicMock()
    change = MagicMock()
    change.diff = "--- a/hello.py\n+++ b/hello.py\n@@ -1 +1 @@\n-print('hello')\n+print('world')\n"
    mock.applied = [change]
    mock.pending_review = []
    mock.errors = []
    return mock


def _make_validator_output(*, passed: bool = True):
    mock = MagicMock()
    mock.overall_passed = passed
    mock.tools = []
    mock.llm_summary = None if passed else "ruff failed"
    mock.model_used_for_summary = ""
    mock.model_dump_json.return_value = json.dumps(
        {
            "overall_passed": passed,
            "tools": [],
        }
    )
    return mock


def _make_risk_result(*, passed: bool = True):
    mock = MagicMock()
    mock.passed = passed
    mock.reasons = [] if passed else ["too many files"]
    return mock


@pytest.fixture
def ci_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    ws = tmp_path / "workspace"
    ws.mkdir()
    return repo, ws


class TestCiExecute:
    """Tests for orchestrator.commands.ci.execute()."""

    def test_scan_failure_not_git_repo(self, tmp_path):
        from orchestrator.commands.ci import execute

        repo = tmp_path / "notgit"
        repo.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()

        result = execute(
            target_path=repo,
            workspace_path=ws,
        )
        assert result.status == "scan_failed"
        assert (ws / "ci_result.json").exists()

    def test_scan_failure_v1_unsupported(self, ci_repo):
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo
        findings = _make_scan_findings(v1_supported=False)

        with patch("orchestrator.scanners.python.scan", return_value=findings):
            result = execute(target_path=repo, workspace_path=ws)

        assert result.status == "scan_failed"
        assert "V1 not supported" in (result.error or "")

    def test_plan_failure_architect_raises(self, ci_repo):
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo
        issue_md = ws / "issue.md"
        issue_md.write_text(
            '---\ntitle: "test"\nnumber: 1\n---\n\nFix bug\n',
            encoding="utf-8",
        )

        with (
            patch("orchestrator.scanners.python.scan", return_value=_make_scan_findings()),
            patch(
                "orchestrator.agents.architect.run_from_issue",
                side_effect=RuntimeError("LLM down"),
            ),
        ):
            result = execute(
                target_path=repo,
                workspace_path=ws,
                issue_file=issue_md,
            )

        assert result.status == "plan_failed"
        assert "LLM down" in (result.error or "")

    def test_plan_failure_risk_gate(self, ci_repo):
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo
        arch_output = _make_arch_output()
        issue_md = ws / "issue.md"
        issue_md.write_text(
            '---\ntitle: "test"\nnumber: 1\n---\n\nFix bug\n',
            encoding="utf-8",
        )

        with (
            patch("orchestrator.scanners.python.scan", return_value=_make_scan_findings()),
            patch(
                "orchestrator.agents.architect.run_from_issue",
                return_value=(arch_output, {"cost_usd": 0}),
            ),
            patch(
                "orchestrator.risk.check_plan_gate",
                return_value=_make_risk_result(passed=False),
            ),
        ):
            result = execute(
                target_path=repo,
                workspace_path=ws,
                issue_file=issue_md,
            )

        assert result.status == "plan_failed"
        assert "Risk gate" in (result.error or "")

    def test_preview_failure_validation_fails(self, ci_repo):
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo
        arch_output = _make_arch_output()
        exec_output = _make_executor_output()
        val_output = _make_validator_output(passed=False)
        issue_md = ws / "issue.md"
        issue_md.write_text(
            '---\ntitle: "test"\nnumber: 1\n---\n\nFix bug\n',
            encoding="utf-8",
        )

        with (
            patch("orchestrator.scanners.python.scan", return_value=_make_scan_findings()),
            patch(
                "orchestrator.agents.architect.run_from_issue",
                return_value=(arch_output, {"cost_usd": 0}),
            ),
            patch(
                "orchestrator.risk.check_plan_gate",
                return_value=_make_risk_result(),
            ),
            patch(
                "orchestrator.risk.check_patch_gate",
                return_value=_make_risk_result(),
            ),
            patch(
                "orchestrator.agents.executor.run",
                return_value=(exec_output, {"cost_usd": 0}),
            ),
            patch("orchestrator.schemas.experiment.Experiment"),
            patch("orchestrator.workspace.WorkspaceManager.write_experiment"),
            patch(
                "orchestrator.validation_workspace.create_validation_workspace",
            ) as mock_val_ws,
            patch(
                "orchestrator.validation_workspace.apply_patch_to_copy",
                return_value=MagicMock(return_code=0),
            ),
            patch(
                "orchestrator.validation_workspace.run_validation_in_copy",
                return_value=val_output,
            ),
        ):
            mock_val_ws.return_value.__enter__ = MagicMock(
                return_value=MagicMock(
                    temporary_root=repo,
                    patch_path=repo / "patch.diff",
                ),
            )
            mock_val_ws.return_value.__exit__ = MagicMock(return_value=False)

            result = execute(
                target_path=repo,
                workspace_path=ws,
                issue_file=issue_md,
            )

        assert result.status == "preview_failed"
        assert not result.validation_passed

    def test_result_file_always_written(self, tmp_path):
        from orchestrator.commands.ci import execute

        repo = tmp_path / "notgit"
        repo.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        result_path = ws / "my_result.json"

        execute(
            target_path=repo,
            workspace_path=ws,
            result_path=result_path,
        )

        assert result_path.exists()
        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert data["status"] == "scan_failed"

    def test_issue_number_preserved_in_result(self, tmp_path):
        from orchestrator.commands.ci import execute

        repo = tmp_path / "notgit"
        repo.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()

        result = execute(
            target_path=repo,
            workspace_path=ws,
            issue_number=42,
        )

        assert result.issue_number == 42

    def test_happy_path_applied(self, ci_repo):
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo
        arch_output = _make_arch_output()
        exec_output = _make_executor_output()
        val_output = _make_validator_output(passed=True)
        issue_md = ws / "issue.md"
        issue_md.write_text(
            '---\ntitle: "test"\nnumber: 1\n---\n\nFix bug\n',
            encoding="utf-8",
        )

        mock_git_ok = MagicMock()
        mock_git_ok.return_code = 0
        mock_git_ok.returncode = 0
        mock_git_ok.stderr = ""
        mock_git_ok.stdout = ""

        with (
            patch("orchestrator.scanners.python.scan", return_value=_make_scan_findings()),
            patch(
                "orchestrator.agents.architect.run_from_issue",
                return_value=(arch_output, {"cost_usd": 0}),
            ),
            patch(
                "orchestrator.risk.check_plan_gate",
                return_value=_make_risk_result(),
            ),
            patch(
                "orchestrator.risk.check_patch_gate",
                return_value=_make_risk_result(),
            ),
            patch(
                "orchestrator.agents.executor.run",
                return_value=(exec_output, {"cost_usd": 0}),
            ),
            patch("orchestrator.schemas.experiment.Experiment"),
            patch("orchestrator.workspace.WorkspaceManager.write_experiment"),
            patch(
                "orchestrator.validation_workspace.create_validation_workspace",
            ) as mock_val_ws,
            patch(
                "orchestrator.validation_workspace.apply_patch_to_copy",
                return_value=MagicMock(return_code=0),
            ),
            patch(
                "orchestrator.validation_workspace.run_validation_in_copy",
                return_value=val_output,
            ),
            patch(
                "orchestrator.git.create_controlled_branch",
                return_value=mock_git_ok,
            ),
            patch(
                "orchestrator.git.apply_patch",
                return_value=mock_git_ok,
            ),
            patch("subprocess.run", return_value=mock_git_ok),
        ):
            mock_val_ws.return_value.__enter__ = MagicMock(
                return_value=MagicMock(
                    temporary_root=repo,
                    patch_path=repo / "patch.diff",
                ),
            )
            mock_val_ws.return_value.__exit__ = MagicMock(return_value=False)

            result = execute(
                target_path=repo,
                workspace_path=ws,
                issue_file=issue_md,
                issue_number=7,
            )

        assert result.status == "applied"
        assert result.validation_passed is True
        assert result.issue_number == 7
        assert "patchforge/" in result.branch

    def test_fail_before_run_id_still_writes_result(self, tmp_path):
        """Regression: _fail must work before run_id is generated."""
        from orchestrator.commands.ci import execute

        repo = tmp_path / "notgit"
        repo.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        result_path = ws / "result.json"

        result = execute(
            target_path=repo,
            workspace_path=ws,
            result_path=result_path,
        )

        assert result.status == "scan_failed"
        assert result_path.exists()
        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert data["run_id"] == ""

    def test_ci_result_schema_roundtrip(self):
        r = CiResult(
            run_id="run_20260630_120000_abc123",
            branch="patchforge/run_20260630_120000_abc123",
            status="applied",
            risk_budget="low",
            affected_files=["src/main.py"],
            validation_passed=True,
            issue_number=42,
        )
        restored = CiResult.model_validate_json(r.model_dump_json())
        assert restored == r
