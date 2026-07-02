"""Tests for B1 — WAL Atomic Apply: crash-safe checkpointing of apply.json."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from orchestrator.schemas.artifacts import ApplyResult
from orchestrator.storage import _wal_write

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_apply_result(**kwargs) -> ApplyResult:
    defaults = {
        "run_id": "run_20240101_000000_abc123",
        "applied_at": datetime.now(timezone.utc),
        "branch": "patchforge/run_20240101_000000_abc123",
        "success": False,
        "status": "applying",
        "pre_apply_head": "deadbeef",
        "pre_apply_branch": "main",
    }
    defaults.update(kwargs)
    return ApplyResult(**defaults)


# ---------------------------------------------------------------------------
# test_wal_written_before_git_apply
# ---------------------------------------------------------------------------


def test_wal_written_before_git_apply(tmp_path: Path) -> None:
    """apply.json with status='applying' must exist on disk before git apply runs."""
    apply_json = tmp_path / "apply.json"

    wal_existed_before_git_apply: list[bool] = []

    apply_result = _make_apply_result()

    def fake_apply_patch(repo_root: Path, patch_path: Path):  # type: ignore[return]
        wal_existed_before_git_apply.append(apply_json.exists())
        result = MagicMock()
        result.return_code = 0
        result.stderr = ""
        return result

    # Write WAL then invoke fake git apply to assert ordering
    _wal_write(apply_result, apply_json)
    fake_apply_patch(tmp_path, tmp_path / "patch.diff")

    assert apply_json.exists(), "apply.json must be written before git apply"

    data = json.loads(apply_json.read_text())
    assert data["status"] == "applying"
    assert wal_existed_before_git_apply == [True], (
        "apply.json was not present when git apply was invoked"
    )


# ---------------------------------------------------------------------------
# test_atomic_write_on_crash
# ---------------------------------------------------------------------------


def test_atomic_write_on_crash(tmp_path: Path) -> None:
    """After _wal_write, no orphaned .tmp file exists and apply.json is valid JSON."""
    apply_json = tmp_path / "apply.json"
    apply_result = _make_apply_result(status="applying")

    _wal_write(apply_result, apply_json)

    # No .tmp leftovers
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Orphaned .tmp files found: {tmp_files}"

    # apply.json is valid JSON with required fields
    assert apply_json.exists()
    data = json.loads(apply_json.read_text())
    assert "status" in data
    assert "run_id" in data


def test_atomic_write_replaces_previous(tmp_path: Path) -> None:
    """_wal_write must atomically replace any previous apply.json."""
    apply_json = tmp_path / "apply.json"

    first = _make_apply_result(status="applying")
    _wal_write(first, apply_json)
    assert json.loads(apply_json.read_text())["status"] == "applying"

    second = _make_apply_result(status="committed_local", success=True)
    _wal_write(second, apply_json)
    assert json.loads(apply_json.read_text())["status"] == "committed_local"

    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Orphaned .tmp files after replace: {tmp_files}"


# ---------------------------------------------------------------------------
# test_each_phase_persists_to_disk
# ---------------------------------------------------------------------------


def test_each_phase_persists_to_disk(tmp_path: Path) -> None:
    """Each phase transition must write the correct status to apply.json."""
    apply_json = tmp_path / "apply.json"

    # Phase 1: status="applying"
    result = _make_apply_result(status="applying")
    _wal_write(result, apply_json)
    data = json.loads(apply_json.read_text())
    assert data["status"] == "applying"
    assert data["success"] is False

    # Simulate phase 2 (Checkpoint 2): pre_apply_diff_backup set
    result.pre_apply_diff_backup = str(tmp_path / "patch.apply-backup.diff")
    _wal_write(result, apply_json)
    data = json.loads(apply_json.read_text())
    assert data["pre_apply_diff_backup"] is not None

    # Phase 5: status="committed_local", success=True
    result.success = True
    result.status = "committed_local"
    _wal_write(result, apply_json)
    data = json.loads(apply_json.read_text())
    assert data["status"] == "committed_local"
    assert data["success"] is True
