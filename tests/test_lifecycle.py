"""Tests for orchestrator.lifecycle.classify_lifecycle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrator.schemas.artifacts import PatchLifecycleState, RunMetadata
from orchestrator.schemas.git import ApplyCheckStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_DIFF = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n"


def _make_workspace(run_id: str, tmp_path: Path, base_commit: str = "a" * 40) -> MagicMock:
    """Return a mock WorkspaceManager whose run_dir points to *tmp_path*."""
    meta = RunMetadata(
        run_id=run_id,
        target_path=str(tmp_path / "repo"),
        workspace_path=str(tmp_path / "ws"),
        base_commit=base_commit,
        branch="main",
        v1_supported=True,
    )
    ws = MagicMock()
    ws.run_dir.return_value = tmp_path / "runs" / run_id
    ws.read_run_json.return_value = meta
    return ws


def _write_patch(run_dir: Path, content: str = _DEFAULT_DIFF) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    patch_path = run_dir / "patch.diff"
    patch_path.write_text(content, encoding="utf-8")
    return patch_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClassifyLifecycle:
    RUN_ID = "run_20260606_000000_abc123"

    def test_stale_when_patch_missing(self, tmp_path):
        """patch.diff not present → STALE."""
        from orchestrator.lifecycle import classify_lifecycle

        ws = _make_workspace(self.RUN_ID, tmp_path)
        # Run dir exists but patch.diff does not.
        (tmp_path / "runs" / self.RUN_ID).mkdir(parents=True)

        result = classify_lifecycle(self.RUN_ID, ws)

        assert result is PatchLifecycleState.STALE

    def test_stale_when_patch_empty(self, tmp_path):
        """patch.diff exists but is empty → STALE."""
        from orchestrator.lifecycle import classify_lifecycle

        ws = _make_workspace(self.RUN_ID, tmp_path)
        _write_patch(tmp_path / "runs" / self.RUN_ID, content="")

        result = classify_lifecycle(self.RUN_ID, ws)

        assert result is PatchLifecycleState.STALE

    def test_stale_when_apply_errors(self, tmp_path):
        """try_apply_dry_run returns ERROR → STALE."""
        from orchestrator.lifecycle import classify_lifecycle

        ws = _make_workspace(self.RUN_ID, tmp_path)
        _write_patch(tmp_path / "runs" / self.RUN_ID)

        with patch(
            "orchestrator.lifecycle.try_apply_dry_run",
            return_value=ApplyCheckStatus.ERROR,
        ):
            result = classify_lifecycle(self.RUN_ID, ws)

        assert result is PatchLifecycleState.STALE

    def test_conflict_when_apply_fails(self, tmp_path):
        """try_apply_dry_run returns CONFLICT → CONFLICT."""
        from orchestrator.lifecycle import classify_lifecycle

        ws = _make_workspace(self.RUN_ID, tmp_path)
        _write_patch(tmp_path / "runs" / self.RUN_ID)

        with patch(
            "orchestrator.lifecycle.try_apply_dry_run",
            return_value=ApplyCheckStatus.CONFLICT,
        ):
            result = classify_lifecycle(self.RUN_ID, ws)

        assert result is PatchLifecycleState.CONFLICT

    def test_valid_when_head_matches_and_passes(self, tmp_path):
        """HEAD == base_commit AND PASSED → VALID."""
        from orchestrator.lifecycle import classify_lifecycle

        base = "a" * 40
        ws = _make_workspace(self.RUN_ID, tmp_path, base_commit=base)
        _write_patch(tmp_path / "runs" / self.RUN_ID)

        with (
            patch(
                "orchestrator.lifecycle.try_apply_dry_run",
                return_value=ApplyCheckStatus.PASSED,
            ),
            patch("orchestrator.lifecycle.get_current_head", return_value=base),
        ):
            result = classify_lifecycle(self.RUN_ID, ws)

        assert result is PatchLifecycleState.VALID

    def test_rebaseable_when_head_differs_and_passes(self, tmp_path):
        """HEAD != base_commit AND PASSED → REBASEABLE."""
        from orchestrator.lifecycle import classify_lifecycle

        base = "a" * 40
        different_head = "b" * 40
        ws = _make_workspace(self.RUN_ID, tmp_path, base_commit=base)
        _write_patch(tmp_path / "runs" / self.RUN_ID)

        with (
            patch(
                "orchestrator.lifecycle.try_apply_dry_run",
                return_value=ApplyCheckStatus.PASSED,
            ),
            patch(
                "orchestrator.lifecycle.get_current_head",
                return_value=different_head,
            ),
        ):
            result = classify_lifecycle(self.RUN_ID, ws)

        assert result is PatchLifecycleState.REBASEABLE

    def test_already_applied_when_tree_equals_head_plus_patch(self, tmp_path):
        """Reverse PASSED + HEAD == base + residue-free → ALREADY_APPLIED."""
        from orchestrator.lifecycle import classify_lifecycle

        base = "a" * 40
        tree_sha = "c" * 40
        ws = _make_workspace(self.RUN_ID, tmp_path, base_commit=base)
        _write_patch(tmp_path / "runs" / self.RUN_ID)

        with (
            patch(
                "orchestrator.lifecycle.try_apply_dry_run",
                return_value=ApplyCheckStatus.CONFLICT,
            ),
            patch(
                "orchestrator.lifecycle.try_apply_dry_run_reverse",
                return_value=ApplyCheckStatus.PASSED,
            ),
            patch("orchestrator.lifecycle.get_current_head", return_value=base),
            patch("orchestrator.lifecycle.head_tree_sha", return_value=tree_sha),
            patch(
                "orchestrator.lifecycle.working_tree_equals_expected_state",
                return_value=True,
            ),
        ):
            result = classify_lifecycle(self.RUN_ID, ws)

        assert result is PatchLifecycleState.ALREADY_APPLIED

    def test_already_applied_with_dirt_stash_present(self, tmp_path):
        """Part 4 lock-in: classify_lifecycle is dirt-agnostic -- a run
        that captured dirt via --allow-dirty (run_metadata.dirt_stash_sha
        set) still classifies as ALREADY_APPLIED under the same three
        conditions as any other resumable run. Dirt-aware behavior lives
        entirely in apply.py; the classifier itself is unmodified."""
        from orchestrator.lifecycle import classify_lifecycle

        base = "a" * 40
        tree_sha = "c" * 40
        ws = _make_workspace(self.RUN_ID, tmp_path, base_commit=base)
        meta = ws.read_run_json.return_value
        meta.dirt_stash_sha = "d" * 40
        _write_patch(tmp_path / "runs" / self.RUN_ID)

        with (
            patch(
                "orchestrator.lifecycle.try_apply_dry_run",
                return_value=ApplyCheckStatus.CONFLICT,
            ),
            patch(
                "orchestrator.lifecycle.try_apply_dry_run_reverse",
                return_value=ApplyCheckStatus.PASSED,
            ),
            patch("orchestrator.lifecycle.get_current_head", return_value=base),
            patch("orchestrator.lifecycle.head_tree_sha", return_value=tree_sha),
            patch(
                "orchestrator.lifecycle.working_tree_equals_expected_state",
                return_value=True,
            ),
        ):
            result = classify_lifecycle(self.RUN_ID, ws)

        assert result is PatchLifecycleState.ALREADY_APPLIED

    def test_conflict_when_reverse_check_fails(self, tmp_path):
        """Reverse CONFLICT → stays CONFLICT even if HEAD matches."""
        from orchestrator.lifecycle import classify_lifecycle

        base = "a" * 40
        ws = _make_workspace(self.RUN_ID, tmp_path, base_commit=base)
        _write_patch(tmp_path / "runs" / self.RUN_ID)

        with (
            patch(
                "orchestrator.lifecycle.try_apply_dry_run",
                return_value=ApplyCheckStatus.CONFLICT,
            ),
            patch(
                "orchestrator.lifecycle.try_apply_dry_run_reverse",
                return_value=ApplyCheckStatus.CONFLICT,
            ),
        ):
            result = classify_lifecycle(self.RUN_ID, ws)

        assert result is PatchLifecycleState.CONFLICT

    def test_conflict_when_head_diverged_from_base(self, tmp_path):
        """Reverse PASSED but HEAD != base → CONFLICT."""
        from orchestrator.lifecycle import classify_lifecycle

        base = "a" * 40
        different_head = "b" * 40
        ws = _make_workspace(self.RUN_ID, tmp_path, base_commit=base)
        _write_patch(tmp_path / "runs" / self.RUN_ID)

        with (
            patch(
                "orchestrator.lifecycle.try_apply_dry_run",
                return_value=ApplyCheckStatus.CONFLICT,
            ),
            patch(
                "orchestrator.lifecycle.try_apply_dry_run_reverse",
                return_value=ApplyCheckStatus.PASSED,
            ),
            patch(
                "orchestrator.lifecycle.get_current_head",
                return_value=different_head,
            ),
        ):
            result = classify_lifecycle(self.RUN_ID, ws)

        assert result is PatchLifecycleState.CONFLICT

    def test_conflict_when_tree_has_extraneous_changes(self, tmp_path):
        """Reverse PASSED + HEAD match but residue in tree → CONFLICT."""
        from orchestrator.lifecycle import classify_lifecycle

        base = "a" * 40
        tree_sha = "c" * 40
        ws = _make_workspace(self.RUN_ID, tmp_path, base_commit=base)
        _write_patch(tmp_path / "runs" / self.RUN_ID)

        with (
            patch(
                "orchestrator.lifecycle.try_apply_dry_run",
                return_value=ApplyCheckStatus.CONFLICT,
            ),
            patch(
                "orchestrator.lifecycle.try_apply_dry_run_reverse",
                return_value=ApplyCheckStatus.PASSED,
            ),
            patch("orchestrator.lifecycle.get_current_head", return_value=base),
            patch("orchestrator.lifecycle.head_tree_sha", return_value=tree_sha),
            patch(
                "orchestrator.lifecycle.working_tree_equals_expected_state",
                return_value=False,
            ),
        ):
            result = classify_lifecycle(self.RUN_ID, ws)

        assert result is PatchLifecycleState.CONFLICT
