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


def test_risk_limits_low_and_medium_unchanged():
    """_risk_limits() collapsed from 3 branches to 2 (#254) — low/medium values
    must be unchanged, and "high" no longer has a dedicated branch."""
    from orchestrator.commands.ci import _risk_limits

    assert _risk_limits("low") == (2, 100)
    assert _risk_limits("medium") == (5, 250)


def test_ci_rejects_high_risk_budget(tmp_path):
    """--risk-budget high is rejected (#254) — CLI no longer accepts it as valid."""
    result = runner.invoke(
        app,
        ["ci", str(tmp_path), "--workspace", str(tmp_path / "ws"), "--risk-budget", "high"],
    )
    assert result.exit_code == 1
    output = result.stdout.lower() + (result.stderr or "").lower()
    assert "risk-budget" in output
    assert "must be 'low' or 'medium'" in output


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
    change.diff = (
        "diff --git a/hello.py b/hello.py\n"
        "--- a/hello.py\n"
        "+++ b/hello.py\n"
        "@@ -1 +1 @@\n"
        "-print('hello')\n"
        "+print('world')\n"
    )
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

    def test_happy_path_carries_provenance_in_ci_result_and_run_json(self, ci_repo, monkeypatch):
        """#241: success-path CiResult and the persisted RunMetadata must both
        carry triggered_by. Failure-path coverage lives in
        test_ci_fail_carries_provenance."""
        from orchestrator.commands.ci import execute
        from orchestrator.workspace import WorkspaceManager

        repo, ws = ci_repo
        monkeypatch.setenv("GITHUB_ACTOR", "octocat")
        arch_output = _make_arch_output()
        exec_output = _make_executor_output()
        val_output = _make_validator_output(passed=True)
        issue_md = ws / "issue.md"
        issue_md.write_text('---\ntitle: "t"\nnumber: 1\n---\n\nFix\n', encoding="utf-8")

        mock_git_ok = MagicMock(return_code=0, returncode=0, stderr="", stdout="")

        with (
            patch("orchestrator.scanners.python.scan", return_value=_make_scan_findings()),
            patch(
                "orchestrator.agents.architect.run_from_issue",
                return_value=(arch_output, {"cost_usd": 0}),
            ),
            patch("orchestrator.risk.check_plan_gate", return_value=_make_risk_result()),
            patch("orchestrator.risk.check_patch_gate", return_value=_make_risk_result()),
            patch(
                "orchestrator.agents.executor.run",
                return_value=(exec_output, {"cost_usd": 0}),
            ),
            patch("orchestrator.schemas.experiment.Experiment"),
            patch("orchestrator.workspace.WorkspaceManager.write_experiment"),
            patch("orchestrator.validation_workspace.create_validation_workspace") as mock_val_ws,
            patch(
                "orchestrator.validation_workspace.apply_patch_to_copy",
                return_value=MagicMock(return_code=0),
            ),
            patch(
                "orchestrator.validation_workspace.run_validation_in_copy",
                return_value=val_output,
            ),
            patch("orchestrator.git.create_controlled_branch", return_value=mock_git_ok),
            patch("orchestrator.git.apply_patch", return_value=mock_git_ok),
            patch("subprocess.run", return_value=mock_git_ok),
        ):
            mock_val_ws.return_value.__enter__ = MagicMock(
                return_value=MagicMock(temporary_root=repo, patch_path=repo / "patch.diff"),
            )
            mock_val_ws.return_value.__exit__ = MagicMock(return_value=False)

            result = execute(
                target_path=repo,
                workspace_path=ws,
                issue_file=issue_md,
                issue_number=7,
            )

        assert result.status == "applied"
        assert result.triggered_by == "github:octocat"

        run_json_data = WorkspaceManager(ws).read_run_json(result.run_id)
        assert run_json_data.triggered_by == "github:octocat"

    def test_force_provider_forwarded_to_executor_and_result(self, ci_repo):
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

        mock_executor_run = MagicMock(return_value=(exec_output, {"cost_usd": 0}))

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
            patch("orchestrator.agents.executor.run", mock_executor_run),
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
                force_provider="claude",
            )

        assert result.status == "applied"
        assert result.force_provider == "claude"
        mock_executor_run.assert_called_once()
        assert mock_executor_run.call_args.kwargs["force_provider"] == "claude"

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

    def test_dirty_tree_blocked_without_allow_dirty(self, ci_repo):
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo
        (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

        result = execute(target_path=repo, workspace_path=ws)

        assert result.status == "scan_failed"
        assert "not clean" in (result.error or "").lower()

    def test_allow_dirty_bypasses_clean_guard(self, ci_repo):
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo
        (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
        findings = _make_scan_findings(v1_supported=False)

        with patch("orchestrator.scanners.python.scan", return_value=findings):
            result = execute(target_path=repo, workspace_path=ws, allow_dirty=True)

        # Reaching the scan stage proves the clean-tree guard was bypassed.
        assert result.status == "scan_failed"
        assert "V1 not supported" in (result.error or "")

    def test_bootstrap_failure_writes_result(self, ci_repo):
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo
        result_path = ws / "boot_result.json"

        with patch(
            "orchestrator.clients.bootstrap.bootstrap_environment",
            side_effect=RuntimeError("boom"),
        ):
            result = execute(
                target_path=repo,
                workspace_path=ws,
                result_path=result_path,
            )

        assert result.status == "scan_failed"
        assert "Bootstrap failed" in (result.error or "")
        assert result_path.exists()

    def test_ci_fail_carries_provenance(self, ci_repo, monkeypatch):
        """#241: CiResult must carry triggered_by even on a failure path,
        not only on success — otherwise failed runs are unauditable."""
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo
        result_path = ws / "fail_result.json"
        monkeypatch.setenv("GITHUB_ACTOR", "octocat")

        with patch(
            "orchestrator.clients.bootstrap.bootstrap_environment",
            side_effect=RuntimeError("boom"),
        ):
            result = execute(
                target_path=repo,
                workspace_path=ws,
                result_path=result_path,
            )

        assert result.status == "scan_failed"
        assert result.triggered_by == "github:octocat"

        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert data["triggered_by"] == "github:octocat"

    def test_apply_failure_preserves_validation_context(self, ci_repo):
        """#5: apply_failed after passing validation must report validation_passed=True."""
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

        def fake_run(cmd, *args, **kwargs):
            m = MagicMock()
            m.stdout = ""
            m.stderr = ""
            if isinstance(cmd, list) and "commit" in cmd:
                m.returncode = 1
                m.stderr = "nothing to commit"
            else:
                m.returncode = 0
            return m

        mock_git_ok = MagicMock()
        mock_git_ok.return_code = 0

        with (
            patch("orchestrator.scanners.python.scan", return_value=_make_scan_findings()),
            patch(
                "orchestrator.agents.architect.run_from_issue",
                return_value=(arch_output, {"cost_usd": 0}),
            ),
            patch("orchestrator.risk.check_plan_gate", return_value=_make_risk_result()),
            patch("orchestrator.risk.check_patch_gate", return_value=_make_risk_result()),
            patch(
                "orchestrator.agents.executor.run",
                return_value=(exec_output, {"cost_usd": 0}),
            ),
            patch("orchestrator.schemas.experiment.Experiment"),
            patch("orchestrator.workspace.WorkspaceManager.write_experiment"),
            patch("orchestrator.validation_workspace.create_validation_workspace") as mock_val_ws,
            patch(
                "orchestrator.validation_workspace.apply_patch_to_copy",
                return_value=MagicMock(return_code=0),
            ),
            patch(
                "orchestrator.validation_workspace.run_validation_in_copy",
                return_value=val_output,
            ),
            patch("orchestrator.git.create_controlled_branch", return_value=mock_git_ok),
            patch("orchestrator.git.apply_patch", return_value=mock_git_ok),
            patch("orchestrator.agents.executor.rollback_to_commit"),
            patch("subprocess.run", side_effect=fake_run),
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

        assert result.status == "apply_failed"
        assert result.validation_passed is True

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

    def test_execute_rejects_invalid_risk_budget(self, tmp_path):
        from orchestrator.commands.ci import execute

        repo = tmp_path / "repo"
        repo.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()

        with pytest.raises(ValueError, match="Invalid risk_budget"):
            execute(target_path=repo, workspace_path=ws, risk_budget="extreme")

    def test_execute_rejects_high_risk_budget(self, tmp_path):
        """execute() called directly (bypassing main.py's CLI check) still
        rejects "high" (#254) — must not silently reach RunMetadata construction."""
        from orchestrator.commands.ci import execute

        repo = tmp_path / "repo"
        repo.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()

        with pytest.raises(ValueError, match="Invalid risk_budget"):
            execute(target_path=repo, workspace_path=ws, risk_budget="high")

    def test_config_load_failure(self, ci_repo):
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo

        with (
            patch("orchestrator.scanners.python.scan", return_value=_make_scan_findings()),
            patch(
                "orchestrator.schemas.config.TargetConfig.load",
                side_effect=RuntimeError("bad config"),
            ),
        ):
            result = execute(target_path=repo, workspace_path=ws)

        assert result.status == "scan_failed"
        assert "Config load failed" in (result.error or "")

    def test_executor_raises(self, ci_repo):
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
            patch("orchestrator.risk.check_plan_gate", return_value=_make_risk_result()),
            patch("orchestrator.schemas.experiment.Experiment"),
            patch("orchestrator.workspace.WorkspaceManager.write_experiment"),
            patch(
                "orchestrator.agents.executor.run",
                side_effect=RuntimeError("executor crash"),
            ),
        ):
            result = execute(
                target_path=repo,
                workspace_path=ws,
                issue_file=issue_md,
            )

        assert result.status == "preview_failed"
        assert "executor crash" in (result.error or "").lower()

    def test_patch_gate_failure(self, ci_repo):
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo
        arch_output = _make_arch_output()
        exec_output = _make_executor_output()
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
            patch("orchestrator.risk.check_plan_gate", return_value=_make_risk_result()),
            patch("orchestrator.schemas.experiment.Experiment"),
            patch("orchestrator.workspace.WorkspaceManager.write_experiment"),
            patch(
                "orchestrator.agents.executor.run",
                return_value=(exec_output, {"cost_usd": 0}),
            ),
            patch(
                "orchestrator.risk.check_patch_gate",
                return_value=_make_risk_result(passed=False),
            ),
        ):
            result = execute(
                target_path=repo,
                workspace_path=ws,
                issue_file=issue_md,
            )

        assert result.status == "preview_failed"
        assert "Patch gate" in (result.error or "")

    def test_experiment_capture_failure(self, ci_repo):
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
            patch("orchestrator.risk.check_plan_gate", return_value=_make_risk_result()),
            patch(
                "orchestrator.schemas.experiment.Experiment",
                side_effect=RuntimeError("capture boom"),
            ),
        ):
            result = execute(
                target_path=repo,
                workspace_path=ws,
                issue_file=issue_md,
            )

        assert result.status == "plan_failed"
        assert "Experiment capture failed" in (result.error or "")

    def test_branch_creation_failure(self, ci_repo):
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

        mock_git_fail = MagicMock()
        mock_git_fail.return_code = 1
        mock_git_fail.stderr = "branch exists"

        with (
            patch("orchestrator.scanners.python.scan", return_value=_make_scan_findings()),
            patch(
                "orchestrator.agents.architect.run_from_issue",
                return_value=(arch_output, {"cost_usd": 0}),
            ),
            patch("orchestrator.risk.check_plan_gate", return_value=_make_risk_result()),
            patch("orchestrator.risk.check_patch_gate", return_value=_make_risk_result()),
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
                return_value=mock_git_fail,
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

        assert result.status == "apply_failed"
        assert "Branch creation failed" in (result.error or "")
        assert result.validation_passed is True

    def test_apply_patch_failure_with_rollback(self, ci_repo):
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

        mock_patch_fail = MagicMock()
        mock_patch_fail.return_code = 1
        mock_patch_fail.stderr = "patch does not apply"

        with (
            patch("orchestrator.scanners.python.scan", return_value=_make_scan_findings()),
            patch(
                "orchestrator.agents.architect.run_from_issue",
                return_value=(arch_output, {"cost_usd": 0}),
            ),
            patch("orchestrator.risk.check_plan_gate", return_value=_make_risk_result()),
            patch("orchestrator.risk.check_patch_gate", return_value=_make_risk_result()),
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
            patch("orchestrator.git.create_controlled_branch", return_value=mock_git_ok),
            patch("orchestrator.git.apply_patch", return_value=mock_patch_fail),
            patch("orchestrator.agents.executor.rollback_to_commit") as mock_rollback,
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

        assert result.status == "apply_failed"
        assert "Patch apply failed" in (result.error or "")
        assert result.validation_passed is True
        mock_rollback.assert_called_once()

    def test_empty_patch_fails(self, ci_repo):
        from orchestrator.commands.ci import execute

        repo, ws = ci_repo
        arch_output = _make_arch_output()
        issue_md = ws / "issue.md"
        issue_md.write_text(
            '---\ntitle: "test"\nnumber: 1\n---\n\nFix bug\n',
            encoding="utf-8",
        )

        empty_exec = MagicMock()
        empty_exec.applied = []
        empty_exec.pending_review = []

        with (
            patch("orchestrator.scanners.python.scan", return_value=_make_scan_findings()),
            patch(
                "orchestrator.agents.architect.run_from_issue",
                return_value=(arch_output, {"cost_usd": 0}),
            ),
            patch("orchestrator.risk.check_plan_gate", return_value=_make_risk_result()),
            patch("orchestrator.schemas.experiment.Experiment"),
            patch("orchestrator.workspace.WorkspaceManager.write_experiment"),
            patch(
                "orchestrator.agents.executor.run",
                return_value=(empty_exec, {"cost_usd": 0}),
            ),
        ):
            result = execute(
                target_path=repo,
                workspace_path=ws,
                issue_file=issue_md,
            )

        assert result.status == "preview_failed"
        assert "empty patch" in (result.error or "").lower()


# ── Integration: targeted staging (no mock on subprocess.run) ──────────


class TestTargetedStaging:
    """Verify git add uses parsed patch files, not -A."""

    def test_stages_only_patch_files_not_untracked(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        # Untracked file that git add -A would have staged
        (repo / "generated.log").write_text("noise\n", encoding="utf-8")

        patch_text = (
            "diff --git a/hello.py b/hello.py\n"
            "--- a/hello.py\n"
            "+++ b/hello.py\n"
            "@@ -1 +1 @@\n"
            "-print('hello')\n"
            "+print('updated')\n"
        )
        patch_path = repo / "patch.diff"
        patch_path.write_text(patch_text, encoding="utf-8", newline="")

        from orchestrator.risk import parse_diff_files

        staged = sorted(parse_diff_files(patch_text))
        assert staged == ["hello.py"]

        # Let git apply modify the file (don't write it manually)
        subprocess.run(
            ["git", "-C", str(repo), "apply", str(patch_path)],
            check=True,
            capture_output=True,
        )

        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-b", "test-branch"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", *staged],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "apply patch"],
            check=True,
            capture_output=True,
        )

        result = subprocess.run(
            ["git", "-C", str(repo), "show", "--name-only", "--format="],
            capture_output=True,
            text=True,
        )
        committed_files = result.stdout.strip().splitlines()
        assert "hello.py" in committed_files
        assert "generated.log" not in committed_files

    def test_stages_new_file_from_patch(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        new_content = "print('brand new')\n"
        (repo / "brand_new.py").write_text(new_content, encoding="utf-8")

        patch_text = (
            "diff --git a/brand_new.py b/brand_new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/brand_new.py\n"
            "@@ -0,0 +1 @@\n"
            "+print('brand new')\n"
        )

        from orchestrator.risk import parse_diff_files

        staged = sorted(parse_diff_files(patch_text))
        assert staged == ["brand_new.py"]

        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-b", "test-new-file"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", *staged],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "add new file"],
            check=True,
            capture_output=True,
        )

        result = subprocess.run(
            ["git", "-C", str(repo), "show", "--name-only", "--format="],
            capture_output=True,
            text=True,
        )
        committed_files = result.stdout.strip().splitlines()
        assert "brand_new.py" in committed_files

    def test_empty_parse_guard(self):
        from orchestrator.risk import parse_diff_files

        assert parse_diff_files("no diff headers here\njust text\n") == set()
