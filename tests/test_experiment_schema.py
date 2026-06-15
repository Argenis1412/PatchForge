"""Tests for Verdict schema and write_verdict utility."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator.schemas.experiment import Verdict
from orchestrator.workspace import WorkspaceManager


def _verdict(**overrides) -> Verdict:
    defaults = dict(
        run_id="run_001",
        status="passed",
        validation_passed=True,
        apply_succeeded=True,
        error_message=None,
        generated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Verdict(**defaults)


def test_passed_verdict():
    v = _verdict(status="passed")
    assert v.run_id == "run_001"
    assert v.status == "passed"
    assert v.validation_passed is True
    assert v.apply_succeeded is True
    assert v.error_message is None
    assert isinstance(v.generated_at, datetime)


def test_failed_verdict():
    v = _verdict(status="failed", validation_passed=False, error_message="ruff: 3 errors")
    assert v.status == "failed"
    assert v.validation_passed is False
    assert v.error_message == "ruff: 3 errors"


def test_round_trip():
    v = _verdict()
    assert v.model_dump() == Verdict.model_validate_json(v.model_dump_json()).model_dump()


def test_write_verdict_writes_files(tmp_path):
    v = _verdict()
    wm = WorkspaceManager(tmp_path)
    wm.create_run_directory("run_001")
    wm.write_verdict("run_001", v)

    json_path = tmp_path / "runs" / "run_001" / "verdict.json"
    assert json_path.exists()
    loaded = Verdict.model_validate_json(json_path.read_text(encoding="utf-8"))
    assert loaded.model_dump() == v.model_dump()

    md_path = tmp_path / "runs" / "run_001" / "verdict.md"
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert v.run_id in content
    assert v.status in content
    assert str(v.validation_passed) in content
    assert str(v.apply_succeeded) in content


def test_write_verdict_file_not_found_error(tmp_path):
    v = _verdict()
    wm = WorkspaceManager(tmp_path)
    with pytest.raises(FileNotFoundError, match="Run directory not found"):
        wm.write_verdict("run_001", v)


def test_write_verdict_rejects_mismatched_run_id(tmp_path):
    v = _verdict(run_id="run_001")
    wm = WorkspaceManager(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        wm.write_verdict("run_999", v)


def test_normalize_git_url():
    from orchestrator.git import normalize_git_url

    # HTTPS urls
    assert normalize_git_url("https://github.com/org/repo.git") == "https://github.com/org/repo"
    assert normalize_git_url("https://github.com/org/repo") == "https://github.com/org/repo"
    assert normalize_git_url("http://github.com/org/repo.git/") == "http://github.com/org/repo"

    # SSH urls
    assert normalize_git_url("git@github.com:org/repo.git") == "https://github.com/org/repo"
    assert normalize_git_url("git@github.com:org/repo") == "https://github.com/org/repo"
    assert normalize_git_url("ssh://git@github.com/org/repo.git") == "https://github.com/org/repo"

    # Casing and slashes normalization
    assert normalize_git_url("HTTPS://GitHub.Com/Org/Repo.GIT") == "https://github.com/org/repo"
    assert normalize_git_url("https://github.com//org///repo") == "https://github.com/org/repo"

    # Local path
    # If it is a local path, it resolves to absolute posix path.
    # We can test with current directory.
    import sys

    resolved = str(Path(".").resolve().as_posix())
    expected_local = resolved.lower() if sys.platform.startswith("win") else resolved
    assert normalize_git_url(".") == expected_local


def test_experiment_schema_round_trip():
    from orchestrator.schemas.architect_output import ArchitectOutput
    from orchestrator.schemas.experiment import Experiment

    plan = ArchitectOutput(
        validated_findings=["finding 1"],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[],
        blockers=[],
    )

    exp = Experiment(
        run_id="run_001",
        plan=plan,
        target_commit_sha="a" * 40,
        repository_identity="https://github.com/org/repo",
        workspace_path=Path("/tmp/workspace"),
    )

    assert exp.schema_version == 1

    # Round-trip check
    dumped = exp.model_dump_json()
    loaded = Experiment.model_validate_json(dumped)
    assert loaded.run_id == exp.run_id
    assert loaded.target_commit_sha == exp.target_commit_sha
    assert loaded.repository_identity == exp.repository_identity
    assert loaded.workspace_path == exp.workspace_path
    assert loaded.schema_version == exp.schema_version
    assert loaded.plan.validated_findings == exp.plan.validated_findings


def test_write_read_experiment(tmp_path):
    from orchestrator.schemas.architect_output import ArchitectOutput
    from orchestrator.schemas.experiment import Experiment

    plan = ArchitectOutput(
        validated_findings=["finding 1"],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[],
        blockers=[],
    )

    exp = Experiment(
        run_id="run_001",
        plan=plan,
        target_commit_sha="a" * 40,
        repository_identity="https://github.com/org/repo",
        workspace_path=tmp_path,
    )

    wm = WorkspaceManager(tmp_path)
    wm.create_run_directory("run_001")

    # Write
    wm.write_experiment("run_001", exp)

    # Read and verify
    loaded = wm.read_experiment("run_001")
    assert loaded.run_id == "run_001"
    assert loaded.target_commit_sha == exp.target_commit_sha
    assert loaded.repository_identity == exp.repository_identity

    # Rejects mismatched run ID
    with pytest.raises(ValueError, match="does not match"):
        wm.write_experiment("run_999", exp)


def test_experiment_verify_success():
    from orchestrator.schemas.architect_output import ArchitectOutput
    from orchestrator.schemas.experiment import Experiment

    plan = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[],
        blockers=[],
    )
    exp = Experiment(
        run_id="run_001",
        plan=plan,
        target_commit_sha="abcdef123456",
        repository_identity="git@github.com:org/repo.git",
        workspace_path=Path("."),
    )

    # Match remote URL formats
    exp.verify("abcdef123456", "https://github.com/org/repo")

    # Match with exact SHA and repo
    exp.verify("abcdef123456", "git@github.com:org/repo.git")


def test_experiment_verify_mismatches():
    from orchestrator.schemas.architect_output import ArchitectOutput
    from orchestrator.schemas.experiment import Experiment

    plan = ArchitectOutput(
        validated_findings=[],
        false_positives=[],
        systemic_risks=[],
        implementation_plan=[],
        blockers=[],
    )
    exp = Experiment(
        run_id="run_001",
        plan=plan,
        target_commit_sha="abcdef123456",
        repository_identity="git@github.com:org/repo.git",
        workspace_path=Path("."),
    )

    # 1. Commit SHA mismatch
    with pytest.raises(ValueError) as excinfo:
        exp.verify("different_sha", "https://github.com/org/repo")
    assert "Commit SHA mismatch" in str(excinfo.value)
    assert "abcdef123456" in str(excinfo.value)
    assert "different_sha" in str(excinfo.value)

    # 2. Repo identity mismatch
    with pytest.raises(ValueError) as excinfo:
        exp.verify("abcdef123456", "https://github.com/another/repo")
    assert "Repository identity mismatch" in str(excinfo.value)
    assert "git@github.com:org/repo.git" in str(excinfo.value)
    assert "https://github.com/another/repo" in str(excinfo.value)
