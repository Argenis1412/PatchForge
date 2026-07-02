"""Tests for B6 — Risk Gate Audit Trail (issue #118).

Covers:
- _is_dangerous() heuristic for all DANGEROUS_PATTERNS
- check_plan_gate() infrastructure escalation to high risk
- risk_gate.json persisted on every call (passed and blocked)
- failure_artifacts populated when gate blocks
- check_patch_gate() persistence via workspace_mgr
- Existing callers unchanged (backward-compat: no workspace_mgr)
"""

import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from orchestrator.risk import (
    DANGEROUS_PATTERNS,
    _is_code_gen,
    _is_dangerous,
    check_patch_gate,
    check_plan_gate,
)
from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.schemas.risk import RISK_GATE_JSON, RiskGateResult

# ── Helpers ───────────────────────────────────────────────────────────────────


def _run_meta(
    risk_budget: str = "medium",
    max_files: int = 5,
    max_diff_lines: int = 500,
) -> RunMetadata:
    return RunMetadata(
        run_id="test-run-b6",
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


def _task(
    task_id: str = "T1",
    files: list[str] | None = None,
    risk_level: str = "low",
) -> Task:
    return Task(
        task_id=task_id,
        title=f"task {task_id}",
        description="",
        files_to_modify=files or ["src/foo.py"],
        priority="high",
        effort="low",
        risk_level=risk_level,
        dependencies=[],
    )


def _mock_workspace(run_id: str = "test-run-b6") -> MagicMock:
    """Return a MagicMock that captures write_artifact calls."""
    ws = MagicMock()
    ws.write_artifact = MagicMock()
    return ws


# ── _is_dangerous() ───────────────────────────────────────────────────────────


class TestIsDangerous:
    @pytest.mark.parametrize(
        "path",
        [
            "Dockerfile",
            "Makefile",
            "docker-compose.yml",
            "Jenkinsfile",
            "requirements.txt",
            "setup.py",
            "setup.cfg",
            "pyproject.toml",
        ],
    )
    def test_exact_basename_match(self, path: str):
        assert _is_dangerous(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            ".github/workflows/deploy.yml",
            ".github/workflows/ci.yaml",
            ".github/workflows/release.yml",
        ],
    )
    def test_directory_prefix_match(self, path: str):
        assert _is_dangerous(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "src/main.py",
            "tests/test_foo.py",
            "README.md",
            "docs/index.rst",
            "src/orchestrator/risk.py",
            ".github/ISSUE_TEMPLATE.md",  # under .github but NOT workflows/
        ],
    )
    def test_safe_files_not_flagged(self, path: str):
        assert _is_dangerous(path) is False

    def test_nested_dangerous_basename(self):
        # Dockerfile nested inside a subdirectory — still dangerous by basename
        assert _is_dangerous("infra/docker/Dockerfile") is True

    @pytest.mark.parametrize(
        "path",
        [
            "Dockerfile.prod",
            "Dockerfile.dev",
            "Dockerfile.ci",
            "Jenkinsfile.production",
            "docker-compose.prod.yml",
            "docker-compose.override.yaml",
            "infra/docker/Dockerfile.prod",
        ],
    )
    def test_infrastructure_variants(self, path: str):
        assert _is_dangerous(path) is True

    def test_all_patterns_covered(self):
        """Every entry in DANGEROUS_PATTERNS triggers _is_dangerous."""
        for pattern in DANGEROUS_PATTERNS:
            path = pattern + "ci.yml" if pattern.endswith("/") else pattern
            assert _is_dangerous(path) is True, f"Pattern not matched: {pattern!r}"


# ── check_plan_gate() — dangerous-file escalation ────────────────────────────


class TestPlanGateDangerousFile:
    def test_dockerfile_escalated_to_high_risk(self):
        meta = _run_meta(risk_budget="medium")
        arch = _arch_output([_task(files=["Dockerfile"], risk_level="low")])
        result = check_plan_gate(meta, arch)
        assert result.passed is False
        assert any("infrastructure" in r for r in result.reasons)
        assert any("Dockerfile" in r for r in result.reasons)

    def test_github_workflow_escalated(self):
        meta = _run_meta(risk_budget="medium")
        arch = _arch_output([_task(files=[".github/workflows/deploy.yml"], risk_level="low")])
        result = check_plan_gate(meta, arch)
        assert result.passed is False
        assert any(".github/workflows/deploy.yml" in r for r in result.reasons)

    def test_safe_file_not_escalated_to_high(self):
        meta = _run_meta(risk_budget="medium")
        arch = _arch_output([_task(files=["src/orchestrator/risk.py"], risk_level="low")])
        result = check_plan_gate(meta, arch)
        # Code-gen floor escalates .py to medium, but not to high
        assert result.passed is True
        task = arch.implementation_plan[0]
        assert task.risk_level == "medium"

    def test_task_risk_level_mutated_to_high(self):
        meta = _run_meta(risk_budget="medium")
        task = _task(files=["requirements.txt"], risk_level="low")
        arch = _arch_output([task])
        check_plan_gate(meta, arch)
        # The task object should have been mutated in-place
        assert task.risk_level == "high"

    def test_mixed_tasks_only_dangerous_blocked(self):
        meta = _run_meta(risk_budget="medium", max_files=10)
        arch = _arch_output(
            [
                _task(task_id="T1", files=["src/foo.py"], risk_level="low"),
                _task(task_id="T2", files=["Makefile"], risk_level="low"),
            ]
        )
        result = check_plan_gate(meta, arch)
        assert result.passed is False
        # Only one infrastructure reason + one high-risk reason
        infra_reasons = [r for r in result.reasons if "infrastructure" in r]
        assert len(infra_reasons) == 1

    def test_already_high_risk_not_double_escalated(self):
        """A task already marked high-risk should not add an extra infra reason."""
        meta = _run_meta(risk_budget="medium")
        task = _task(files=["Dockerfile"], risk_level="high")
        arch = _arch_output([task])
        result = check_plan_gate(meta, arch)
        infra_reasons = [r for r in result.reasons if "infrastructure" in r]
        # Heuristic fires on the file, adding one infra reason
        assert len(infra_reasons) == 1


# ── risk_gate.json persistence ────────────────────────────────────────────────


class TestRiskGateJsonPersistence:
    def test_plan_gate_writes_artifact_on_pass(self):
        meta = _run_meta()
        arch = _arch_output([_task(files=["src/foo.py"])])
        ws = _mock_workspace(meta.run_id)
        result = check_plan_gate(meta, arch, workspace_mgr=ws)
        assert result.passed is True
        ws.write_artifact.assert_called_once_with(
            meta.run_id, RISK_GATE_JSON, result.model_dump_json(indent=2)
        )

    def test_plan_gate_writes_artifact_on_block(self):
        meta = _run_meta(risk_budget="low")
        arch = _arch_output([_task(files=["src/foo.py"], risk_level="medium")])
        ws = _mock_workspace(meta.run_id)
        result = check_plan_gate(meta, arch, workspace_mgr=ws)
        assert result.passed is False
        ws.write_artifact.assert_called_once()
        _, artifact_name, content = ws.write_artifact.call_args[0]
        assert artifact_name == RISK_GATE_JSON
        parsed = json.loads(content)
        assert parsed["passed"] is False
        assert parsed["gate"] == "plan"

    def test_patch_gate_writes_artifact_on_pass(self):
        meta = _run_meta()
        ws = _mock_workspace(meta.run_id)
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        result = check_patch_gate(meta, diff, workspace_mgr=ws)
        assert result.passed is True
        ws.write_artifact.assert_called_once_with(
            meta.run_id, RISK_GATE_JSON, result.model_dump_json(indent=2)
        )

    def test_patch_gate_writes_artifact_on_block(self):
        meta = _run_meta(max_diff_lines=1)
        ws = _mock_workspace(meta.run_id)
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n+extra\n"
        result = check_patch_gate(meta, diff, workspace_mgr=ws)
        assert result.passed is False
        ws.write_artifact.assert_called_once()

    def test_no_workspace_mgr_no_write(self):
        """Without workspace_mgr the function must not raise and must not write."""
        meta = _run_meta()
        arch = _arch_output([_task()])
        # Should succeed silently
        result = check_plan_gate(meta, arch, workspace_mgr=None)
        assert isinstance(result, RiskGateResult)

    def test_artifact_content_is_valid_json(self):
        meta = _run_meta(risk_budget="low")
        arch = _arch_output([_task(files=["pyproject.toml"], risk_level="low")])
        ws = _mock_workspace(meta.run_id)
        check_plan_gate(meta, arch, workspace_mgr=ws)
        _, _, content = ws.write_artifact.call_args[0]
        parsed = json.loads(content)
        assert "passed" in parsed
        assert "gate" in parsed
        assert "reasons" in parsed


# ── failure_artifacts population ──────────────────────────────────────────────


class TestFailureArtifacts:
    def test_plan_gate_block_adds_risk_gate_to_failure_artifacts(self):
        meta = _run_meta(risk_budget="low")
        arch = _arch_output([_task(files=["src/foo.py"], risk_level="medium")])
        ws = _mock_workspace(meta.run_id)
        check_plan_gate(meta, arch, workspace_mgr=ws)
        assert meta.failure_artifacts is not None
        assert RISK_GATE_JSON in meta.failure_artifacts

    def test_plan_gate_pass_does_not_touch_failure_artifacts(self):
        meta = _run_meta()
        arch = _arch_output([_task(files=["src/foo.py"])])
        ws = _mock_workspace(meta.run_id)
        check_plan_gate(meta, arch, workspace_mgr=ws)
        assert not meta.failure_artifacts  # None or empty

    def test_patch_gate_block_adds_risk_gate_to_failure_artifacts(self):
        meta = _run_meta(max_diff_lines=1)
        ws = _mock_workspace(meta.run_id)
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n+extra\n"
        check_patch_gate(meta, diff, workspace_mgr=ws)
        assert meta.failure_artifacts is not None
        assert RISK_GATE_JSON in meta.failure_artifacts

    def test_patch_gate_pass_does_not_touch_failure_artifacts(self):
        meta = _run_meta()
        ws = _mock_workspace(meta.run_id)
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        check_patch_gate(meta, diff, workspace_mgr=ws)
        assert not meta.failure_artifacts

    def test_failure_artifacts_no_duplicates(self):
        """Calling the gate twice on a blocked result must not duplicate entries."""
        meta = _run_meta(risk_budget="low")
        arch = _arch_output([_task(files=["src/foo.py"], risk_level="medium")])
        ws = _mock_workspace(meta.run_id)
        check_plan_gate(meta, arch, workspace_mgr=ws)
        # Simulate re-run (e.g. retry): mutate arch so risk stays medium
        arch2 = _arch_output([_task(files=["src/foo.py"], risk_level="medium")])
        check_plan_gate(meta, arch2, workspace_mgr=ws)
        assert meta.failure_artifacts is not None
        assert meta.failure_artifacts.count(RISK_GATE_JSON) == 1

    def test_failure_artifacts_appended_to_existing_list(self):
        """If failure_artifacts already has entries, risk_gate.json is appended."""
        meta = _run_meta(risk_budget="low")
        meta.failure_artifacts = ["some_other.json"]
        arch = _arch_output([_task(files=["src/foo.py"], risk_level="medium")])
        ws = _mock_workspace(meta.run_id)
        check_plan_gate(meta, arch, workspace_mgr=ws)
        assert "some_other.json" in meta.failure_artifacts
        assert RISK_GATE_JSON in meta.failure_artifacts


# ── Backward-compat: existing unit-test call sites ────────────────────────────


class TestBackwardCompat:
    """Ensure the old 2-argument call signature still works."""

    def test_check_plan_gate_no_workspace_mgr(self):
        meta = _run_meta()
        arch = _arch_output([_task()])
        result = check_plan_gate(meta, arch)
        assert isinstance(result, RiskGateResult)

    def test_check_patch_gate_no_workspace_mgr(self):
        meta = _run_meta()
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        result = check_patch_gate(meta, diff)
        assert isinstance(result, RiskGateResult)


# ── Task Literal validation (issue #156) ─────────────────────────────────────


class TestTaskLiteralValidation:
    def test_valid_risk_level_accepted(self):
        for level in ("low", "medium", "high"):
            t = Task(
                task_id="T1",
                title="t",
                description="",
                files_to_modify=["a.py"],
                priority="low",
                effort="low",
                risk_level=level,
                dependencies=[],
            )
            assert t.risk_level == level

    def test_invalid_risk_level_rejected(self):
        with pytest.raises(ValidationError):
            Task(
                task_id="T1",
                title="t",
                description="",
                files_to_modify=["a.py"],
                priority="low",
                effort="low",
                risk_level="invalid",
                dependencies=[],
            )

    def test_uppercase_risk_level_rejected(self):
        with pytest.raises(ValidationError):
            Task(
                task_id="T1",
                title="t",
                description="",
                files_to_modify=["a.py"],
                priority="low",
                effort="low",
                risk_level="MEDIUM",
                dependencies=[],
            )

    def test_invalid_priority_rejected(self):
        with pytest.raises(ValidationError):
            Task(
                task_id="T1",
                title="t",
                description="",
                files_to_modify=["a.py"],
                priority="critical",
                effort="low",
                risk_level="low",
                dependencies=[],
            )

    def test_invalid_effort_rejected(self):
        with pytest.raises(ValidationError):
            Task(
                task_id="T1",
                title="t",
                description="",
                files_to_modify=["a.py"],
                priority="low",
                effort="trivial",
                risk_level="low",
                dependencies=[],
            )


# ── Code-gen risk floor (issue #156) ─────────────────────────────────────────


class TestCodeGenRiskFloor:
    def test_is_code_gen_with_py_file(self):
        t = _task(files=["src/main.py"])
        assert _is_code_gen(t) is True

    def test_is_code_gen_with_ts_file(self):
        t = _task(files=["app/index.ts"])
        assert _is_code_gen(t) is True

    def test_is_code_gen_with_tsx_file(self):
        t = _task(files=["components/App.tsx"])
        assert _is_code_gen(t) is True

    def test_is_code_gen_with_js_file(self):
        t = _task(files=["lib/utils.js"])
        assert _is_code_gen(t) is True

    def test_is_code_gen_with_jsx_file(self):
        t = _task(files=["components/Button.jsx"])
        assert _is_code_gen(t) is True

    def test_not_code_gen_doc_only(self):
        t = _task(files=["README.md"])
        assert _is_code_gen(t) is False

    def test_not_code_gen_changelog(self):
        t = _task(files=["CHANGELOG.md", "docs/guide.rst"])
        assert _is_code_gen(t) is False

    def test_mixed_files_is_code_gen(self):
        t = _task(files=["README.md", "src/main.py"])
        assert _is_code_gen(t) is True

    def test_low_risk_code_gen_escalated_to_medium(self):
        meta = _run_meta(risk_budget="medium")
        task = _task(files=["src/foo.py"], risk_level="low")
        arch = _arch_output([task])
        check_plan_gate(meta, arch)
        assert task.risk_level == "medium"

    def test_low_risk_doc_task_not_escalated(self):
        meta = _run_meta(risk_budget="medium")
        task = _task(files=["README.md"], risk_level="low")
        arch = _arch_output([task])
        result = check_plan_gate(meta, arch)
        assert task.risk_level == "low"
        assert result.passed is True

    def test_high_risk_not_downgraded(self):
        meta = _run_meta(risk_budget="medium")
        task = _task(files=["src/foo.py"], risk_level="high")
        arch = _arch_output([task])
        check_plan_gate(meta, arch)
        assert task.risk_level == "high"

    def test_medium_risk_not_changed(self):
        meta = _run_meta(risk_budget="medium")
        task = _task(files=["src/foo.py"], risk_level="medium")
        arch = _arch_output([task])
        check_plan_gate(meta, arch)
        assert task.risk_level == "medium"

    def test_escalated_medium_blocked_by_low_budget(self):
        meta = _run_meta(risk_budget="low")
        task = _task(files=["src/foo.py"], risk_level="low")
        arch = _arch_output([task])
        result = check_plan_gate(meta, arch)
        assert task.risk_level == "medium"
        assert result.passed is False
        assert any("medium-risk" in r for r in result.reasons)
