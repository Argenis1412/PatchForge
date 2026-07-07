"""Tests for D-001: validate_plan_paths rejects plans with phantom/unsafe paths."""

from pathlib import Path

from orchestrator.plan_validation import validate_plan_paths
from orchestrator.schemas.architect_output import ArchitectOutput, Task


def _make_plan(files: list[str]) -> ArchitectOutput:
    return ArchitectOutput(
        validated_findings=["f"],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[
            Task(
                task_id="T1",
                title="Task",
                description="desc",
                files_to_modify=files,
                priority="low",
                effort="low",
                risk_level="low",
            )
        ],
        blockers=[],
    )


def _make_multi_task_plan(tasks: list[tuple[str, list[str]]]) -> ArchitectOutput:
    return ArchitectOutput(
        validated_findings=["f"],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[
            Task(
                task_id=tid,
                title=f"Task {tid}",
                description="desc",
                files_to_modify=files,
                priority="low",
                effort="low",
                risk_level="low",
            )
            for tid, files in tasks
        ],
        blockers=[],
    )


class TestPhantomPaths:
    def test_nonexistent_file_in_nonexistent_dir_rejected(self, tmp_path: Path):
        plan = _make_plan(["nonexistent_dir/test_risk.py"])
        reasons = validate_plan_paths(plan, tmp_path)
        assert len(reasons) == 1
        assert "non-existent paths" in reasons[0]
        assert "nonexistent_dir/test_risk.py" in reasons[0]

    def test_existing_file_passes(self, tmp_path: Path):
        (tmp_path / "real.py").write_text("x", encoding="utf-8")
        plan = _make_plan(["real.py"])
        reasons = validate_plan_paths(plan, tmp_path)
        assert reasons == []

    def test_new_file_in_existing_dir_passes(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        plan = _make_plan(["src/new_file.py"])
        reasons = validate_plan_paths(plan, tmp_path)
        assert reasons == []


class TestEmptyFilesToModify:
    def test_empty_list_rejected(self, tmp_path: Path):
        plan = _make_plan([])
        reasons = validate_plan_paths(plan, tmp_path)
        assert len(reasons) == 1
        assert "empty files_to_modify" in reasons[0]


class TestUnsafePaths:
    def test_parent_traversal_rejected(self, tmp_path: Path):
        plan = _make_plan(["../etc/passwd"])
        reasons = validate_plan_paths(plan, tmp_path)
        assert any("unsafe path" in r for r in reasons)

    def test_absolute_path_rejected(self, tmp_path: Path):
        plan = _make_plan(["/etc/passwd"])
        reasons = validate_plan_paths(plan, tmp_path)
        assert any("unsafe path" in r for r in reasons)


class TestDeduplication:
    def test_duplicate_phantom_paths_deduplicated(self, tmp_path: Path):
        plan = _make_multi_task_plan(
            [
                ("T1", ["ghost_dir/a.py"]),
                ("T2", ["ghost_dir/a.py"]),
            ]
        )
        reasons = validate_plan_paths(plan, tmp_path)
        phantom_reasons = [r for r in reasons if "non-existent" in r]
        assert len(phantom_reasons) == 1


class TestRegression:
    def test_plan_with_all_existing_files_passes(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        (tmp_path / "b.py").write_text("y", encoding="utf-8")
        plan = _make_multi_task_plan(
            [
                ("T1", ["a.py"]),
                ("T2", ["b.py"]),
            ]
        )
        reasons = validate_plan_paths(plan, tmp_path)
        assert reasons == []
