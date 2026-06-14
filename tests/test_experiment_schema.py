"""Tests for Verdict schema and write_verdict utility."""

from datetime import datetime, timezone

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
