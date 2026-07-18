"""Tests for resumable apply — ALREADY_APPLIED lifecycle state handling."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from orchestrator.commands.apply import _hydrate_apply_result_for_resume
from orchestrator.schemas.artifacts import ApplyResult
from orchestrator.storage import _wal_write

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_apply_result(**kwargs) -> ApplyResult:
    defaults = {
        "run_id": "run_20260101_000000_abc123",
        "applied_at": datetime.now(timezone.utc),
        "branch": "patchforge/run_20260101_000000_abc123",
        "success": False,
        "status": "applying",
        "pre_apply_head": "deadbeef" * 5,
        "pre_apply_branch": "main",
    }
    defaults.update(kwargs)
    return ApplyResult(**defaults)


def _write_wal(run_dir: Path, result: ApplyResult) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    wal_path = run_dir / "apply.json"
    _wal_write(result, wal_path)
    return wal_path


# ---------------------------------------------------------------------------
# _hydrate_apply_result_for_resume
# ---------------------------------------------------------------------------


class TestHydrateApplyResult:
    def test_returns_none_when_wal_missing(self, tmp_path):
        assert _hydrate_apply_result_for_resume(tmp_path) is None

    def test_returns_none_when_status_is_not_applying(self, tmp_path):
        result = _make_apply_result(status="applied")
        backup = tmp_path / "patch.apply-backup.diff"
        backup.write_text("diff", encoding="utf-8")
        result.pre_apply_diff_backup = str(backup)
        _write_wal(tmp_path, result)

        assert _hydrate_apply_result_for_resume(tmp_path) is None

    def test_returns_none_when_backup_diff_missing(self, tmp_path):
        result = _make_apply_result(
            pre_apply_diff_backup=str(tmp_path / "nonexistent.diff"),
        )
        _write_wal(tmp_path, result)

        assert _hydrate_apply_result_for_resume(tmp_path) is None

    def test_returns_none_when_backup_diff_field_empty(self, tmp_path):
        result = _make_apply_result(pre_apply_diff_backup=None)
        _write_wal(tmp_path, result)

        assert _hydrate_apply_result_for_resume(tmp_path) is None

    def test_returns_result_when_all_preconditions_met(self, tmp_path):
        backup = tmp_path / "patch.apply-backup.diff"
        backup.write_text("diff content", encoding="utf-8")
        result = _make_apply_result(pre_apply_diff_backup=str(backup))
        _write_wal(tmp_path, result)

        hydrated = _hydrate_apply_result_for_resume(tmp_path)

        assert hydrated is not None
        assert hydrated.status == "applying"
        assert hydrated.pre_apply_head == result.pre_apply_head
        assert hydrated.branch == result.branch

    def test_preserves_dirty_stash_fields(self, tmp_path):
        backup = tmp_path / "patch.apply-backup.diff"
        backup.write_text("diff content", encoding="utf-8")
        stash_sha = "abcdef" * 7 + "ab"
        stash_tree = "fedcba" * 7 + "fe"
        result = _make_apply_result(
            pre_apply_diff_backup=str(backup),
            pre_apply_dirty_stash=stash_sha,
            pre_apply_dirty_stash_tree=stash_tree,
        )
        _write_wal(tmp_path, result)

        hydrated = _hydrate_apply_result_for_resume(tmp_path)

        assert hydrated is not None
        assert hydrated.pre_apply_dirty_stash == stash_sha
        assert hydrated.pre_apply_dirty_stash_tree == stash_tree

    def test_returns_none_on_corrupt_json(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "apply.json").write_text("not json", encoding="utf-8")

        assert _hydrate_apply_result_for_resume(tmp_path) is None

    def test_preserves_backup_pointer_across_resume(self, tmp_path):
        """Ataque 2: backup pointer must survive crash→resume→crash cycle."""
        backup = tmp_path / "patch.apply-backup.diff"
        backup.write_text("original diff", encoding="utf-8")
        original_backup_path = str(backup)

        result = _make_apply_result(pre_apply_diff_backup=original_backup_path)
        _write_wal(tmp_path, result)

        hydrated = _hydrate_apply_result_for_resume(tmp_path)
        assert hydrated is not None

        hydrated.applied_at = datetime.now(timezone.utc)
        _wal_write(hydrated, tmp_path / "apply.json")

        hydrated2 = _hydrate_apply_result_for_resume(tmp_path)
        assert hydrated2 is not None
        assert hydrated2.pre_apply_diff_backup == original_backup_path


# ---------------------------------------------------------------------------
# Resume path integration (mocked)
# ---------------------------------------------------------------------------


class TestApplyResumePath:
    RUN_ID = "run_20260101_000000_abc123"

    def _make_run_metadata(self, tmp_path, base_commit="deadbeef" * 5):
        from orchestrator.schemas.artifacts import RunMetadata

        return RunMetadata(
            run_id=self.RUN_ID,
            target_path=str(tmp_path / "repo"),
            workspace_path=str(tmp_path / "ws"),
            base_commit=base_commit,
            branch="main",
            status="previewed",
            v1_supported=True,
            patch_checksum="abc123",
        )

    def test_apply_aborts_when_wal_not_hydratable(self, tmp_path):
        """Resume aborts cleanly when apply.json cannot be hydrated."""
        from orchestrator.commands.apply import _execute_resume

        run_dir = tmp_path / "runs" / self.RUN_ID
        run_dir.mkdir(parents=True)
        # No apply.json at all

        with pytest.raises(typer.Exit) as exc:
            _execute_resume(
                run_id=self.RUN_ID,
                run_metadata=self._make_run_metadata(tmp_path),
                target_path=tmp_path / "repo",
                workspace_mgr=MagicMock(),
                config=MagicMock(),
                logs_dir=tmp_path / "logs",
                run_dir=run_dir,
                patch_path=run_dir / "patch.diff",
                worker_id=None,
                coordination_db_dir=None,
                run_validator=MagicMock(),
                rollback_to_commit=MagicMock(),
                rollback_error_cls=Exception,
                log_event=MagicMock(),
                log_failure=MagicMock(),
                current_head=MagicMock(),
                current_branch=MagicMock(),
                stash_apply=MagicMock(),
            )

        assert exc.value.exit_code == 1

    def test_apply_aborts_split_brain_branch(self, tmp_path):
        """Ataque 3: current branch differs from WAL → abort."""
        from orchestrator.commands.apply import _execute_resume

        run_dir = tmp_path / "runs" / self.RUN_ID
        backup = run_dir / "patch.apply-backup.diff"
        run_dir.mkdir(parents=True)
        backup.write_text("diff", encoding="utf-8")

        wal_branch = f"patchforge/{self.RUN_ID}"
        result = _make_apply_result(
            branch=wal_branch,
            pre_apply_diff_backup=str(backup),
        )
        _write_wal(run_dir, result)

        with pytest.raises(typer.Exit) as exc:
            _execute_resume(
                run_id=self.RUN_ID,
                run_metadata=self._make_run_metadata(tmp_path),
                target_path=tmp_path / "repo",
                workspace_mgr=MagicMock(),
                config=MagicMock(),
                logs_dir=tmp_path / "logs",
                run_dir=run_dir,
                patch_path=run_dir / "patch.diff",
                worker_id=None,
                coordination_db_dir=None,
                run_validator=MagicMock(),
                rollback_to_commit=MagicMock(),
                rollback_error_cls=Exception,
                log_event=MagicMock(),
                log_failure=MagicMock(),
                current_head=MagicMock(return_value=result.pre_apply_head),
                current_branch=MagicMock(return_value="main"),
                stash_apply=MagicMock(),
            )

        assert exc.value.exit_code == 1

    def test_apply_aborts_split_brain_head(self, tmp_path):
        """Ataque 3: current HEAD differs from WAL → abort."""
        from orchestrator.commands.apply import _execute_resume

        run_dir = tmp_path / "runs" / self.RUN_ID
        backup = run_dir / "patch.apply-backup.diff"
        run_dir.mkdir(parents=True)
        backup.write_text("diff", encoding="utf-8")

        wal_branch = f"patchforge/{self.RUN_ID}"
        result = _make_apply_result(
            branch=wal_branch,
            pre_apply_diff_backup=str(backup),
        )
        _write_wal(run_dir, result)

        with pytest.raises(typer.Exit) as exc:
            _execute_resume(
                run_id=self.RUN_ID,
                run_metadata=self._make_run_metadata(tmp_path),
                target_path=tmp_path / "repo",
                workspace_mgr=MagicMock(),
                config=MagicMock(),
                logs_dir=tmp_path / "logs",
                run_dir=run_dir,
                patch_path=run_dir / "patch.diff",
                worker_id=None,
                coordination_db_dir=None,
                run_validator=MagicMock(),
                rollback_to_commit=MagicMock(),
                rollback_error_cls=Exception,
                log_event=MagicMock(),
                log_failure=MagicMock(),
                current_head=MagicMock(return_value="different" * 5),
                current_branch=MagicMock(return_value=wal_branch),
                stash_apply=MagicMock(),
            )

        assert exc.value.exit_code == 1

    def test_apply_resumes_successfully(self, tmp_path):
        """Happy resume: isolation passes, validator passes → success."""
        from orchestrator.commands.apply import _execute_resume

        run_dir = tmp_path / "runs" / self.RUN_ID
        backup = run_dir / "patch.apply-backup.diff"
        run_dir.mkdir(parents=True)
        backup.write_text("diff", encoding="utf-8")

        wal_branch = f"patchforge/{self.RUN_ID}"
        pre_head = "deadbeef" * 5
        result = _make_apply_result(
            branch=wal_branch,
            pre_apply_head=pre_head,
            pre_apply_diff_backup=str(backup),
        )
        _write_wal(run_dir, result)

        mock_val_output = MagicMock()
        mock_val_output.overall_passed = True
        mock_val_output.model_dump_json.return_value = "{}"
        mock_validator = MagicMock(return_value=(mock_val_output, None))

        mock_ws = MagicMock()
        meta = self._make_run_metadata(tmp_path, base_commit=pre_head)

        _execute_resume(
            run_id=self.RUN_ID,
            run_metadata=meta,
            target_path=tmp_path / "repo",
            workspace_mgr=mock_ws,
            config=MagicMock(),
            logs_dir=tmp_path / "logs",
            run_dir=run_dir,
            patch_path=run_dir / "patch.diff",
            worker_id=None,
            coordination_db_dir=None,
            run_validator=mock_validator,
            rollback_to_commit=MagicMock(),
            rollback_error_cls=Exception,
            log_event=MagicMock(),
            log_failure=MagicMock(),
            current_head=MagicMock(return_value=pre_head),
            current_branch=MagicMock(return_value=wal_branch),
            stash_apply=MagicMock(),
        )

        assert meta.status == "applied"
        assert meta.apply_status == "success"

        wal_data = json.loads((run_dir / "apply.json").read_text(encoding="utf-8"))
        assert wal_data["status"] == "applied"
        assert wal_data["success"] is True

    def test_apply_resume_rollback_on_validation_failure(self, tmp_path):
        """Resume + validator fail → rollback + exit 1."""
        from orchestrator.commands.apply import _execute_resume

        run_dir = tmp_path / "runs" / self.RUN_ID
        backup = run_dir / "patch.apply-backup.diff"
        run_dir.mkdir(parents=True)
        backup.write_text("diff", encoding="utf-8")

        wal_branch = f"patchforge/{self.RUN_ID}"
        pre_head = "deadbeef" * 5
        result = _make_apply_result(
            branch=wal_branch,
            pre_apply_head=pre_head,
            pre_apply_diff_backup=str(backup),
        )
        _write_wal(run_dir, result)

        mock_val_output = MagicMock()
        mock_val_output.overall_passed = False
        mock_val_output.model_dump_json.return_value = "{}"
        mock_val_output.model_dump.return_value = {"overall_passed": False}
        mock_validator = MagicMock(return_value=(mock_val_output, None))

        mock_rollback = MagicMock()
        mock_ws = MagicMock()
        meta = self._make_run_metadata(tmp_path, base_commit=pre_head)

        with pytest.raises(typer.Exit) as exc:
            _execute_resume(
                run_id=self.RUN_ID,
                run_metadata=meta,
                target_path=tmp_path / "repo",
                workspace_mgr=mock_ws,
                config=MagicMock(),
                logs_dir=tmp_path / "logs",
                run_dir=run_dir,
                patch_path=run_dir / "patch.diff",
                worker_id=None,
                coordination_db_dir=None,
                run_validator=mock_validator,
                rollback_to_commit=mock_rollback,
                rollback_error_cls=Exception,
                log_event=MagicMock(),
                log_failure=MagicMock(),
                current_head=MagicMock(return_value=pre_head),
                current_branch=MagicMock(return_value=wal_branch),
                stash_apply=MagicMock(),
            )

        assert exc.value.exit_code == 1
        mock_rollback.assert_called_once()
        assert meta.status == "failed"

    def test_apply_resume_calls_validator_with_config(self, tmp_path):
        """Ataque 6: validator must receive config from TargetConfig.load."""
        from orchestrator.commands.apply import _execute_resume

        run_dir = tmp_path / "runs" / self.RUN_ID
        backup = run_dir / "patch.apply-backup.diff"
        run_dir.mkdir(parents=True)
        backup.write_text("diff", encoding="utf-8")

        wal_branch = f"patchforge/{self.RUN_ID}"
        pre_head = "deadbeef" * 5
        result = _make_apply_result(
            branch=wal_branch,
            pre_apply_head=pre_head,
            pre_apply_diff_backup=str(backup),
        )
        _write_wal(run_dir, result)

        mock_val_output = MagicMock()
        mock_val_output.overall_passed = True
        mock_val_output.model_dump_json.return_value = "{}"
        mock_validator = MagicMock(return_value=(mock_val_output, None))

        sentinel_config = MagicMock(name="sentinel_config")

        _execute_resume(
            run_id=self.RUN_ID,
            run_metadata=self._make_run_metadata(tmp_path, base_commit=pre_head),
            target_path=tmp_path / "repo",
            workspace_mgr=MagicMock(),
            config=sentinel_config,
            logs_dir=tmp_path / "logs",
            run_dir=run_dir,
            patch_path=run_dir / "patch.diff",
            worker_id=None,
            coordination_db_dir=None,
            run_validator=mock_validator,
            rollback_to_commit=MagicMock(),
            rollback_error_cls=Exception,
            log_event=MagicMock(),
            log_failure=MagicMock(),
            current_head=MagicMock(return_value=pre_head),
            current_branch=MagicMock(return_value=wal_branch),
            stash_apply=MagicMock(),
        )

        mock_validator.assert_called_once_with(config=sentinel_config)

    def test_apply_resume_emits_log_events(self, tmp_path):
        """Validator re-run must be wrapped with observability events."""
        from orchestrator.commands.apply import _execute_resume

        run_dir = tmp_path / "runs" / self.RUN_ID
        backup = run_dir / "patch.apply-backup.diff"
        run_dir.mkdir(parents=True)
        backup.write_text("diff", encoding="utf-8")

        wal_branch = f"patchforge/{self.RUN_ID}"
        pre_head = "deadbeef" * 5
        result = _make_apply_result(
            branch=wal_branch,
            pre_apply_head=pre_head,
            pre_apply_diff_backup=str(backup),
        )
        _write_wal(run_dir, result)

        mock_val_output = MagicMock()
        mock_val_output.overall_passed = True
        mock_val_output.model_dump_json.return_value = "{}"
        mock_validator = MagicMock(return_value=(mock_val_output, None))
        mock_log_event = MagicMock()

        _execute_resume(
            run_id=self.RUN_ID,
            run_metadata=self._make_run_metadata(tmp_path, base_commit=pre_head),
            target_path=tmp_path / "repo",
            workspace_mgr=MagicMock(),
            config=MagicMock(),
            logs_dir=tmp_path / "logs",
            run_dir=run_dir,
            patch_path=run_dir / "patch.diff",
            worker_id=None,
            coordination_db_dir=None,
            run_validator=mock_validator,
            rollback_to_commit=MagicMock(),
            rollback_error_cls=Exception,
            log_event=mock_log_event,
            log_failure=MagicMock(),
            current_head=MagicMock(return_value=pre_head),
            current_branch=MagicMock(return_value=wal_branch),
            stash_apply=MagicMock(),
        )

        event_names = [
            c.kwargs.get("event") or c[1].get("event", "") for c in mock_log_event.call_args_list
        ]
        assert "stage_start" in event_names
        assert "post_apply_validation_start" in event_names
        assert "post_apply_validation_end" in event_names
        assert "stage_end" in event_names


# ---------------------------------------------------------------------------
# Dirt snapshot in happy-path
# ---------------------------------------------------------------------------


class TestDirtSnapshot:
    def test_stash_create_called_when_dirty_and_allow_dirty(self, tmp_path):
        """§2.5: stash_create_untracked called when --allow-dirty + dirty tree."""
        import hashlib

        from orchestrator.commands.apply import execute as apply_execute
        from orchestrator.schemas.artifacts import PatchLifecycleState, RunMetadata

        run_id = "run_20260101_000000_abc123"
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "patch.diff").write_text(
            "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n",
            encoding="utf-8",
        )

        patch_content = (run_dir / "patch.diff").read_text(encoding="utf-8")
        checksum = hashlib.sha256(patch_content.encode("utf-8")).hexdigest()

        meta = RunMetadata(
            run_id=run_id,
            target_path=str(tmp_path / "repo"),
            workspace_path=str(tmp_path / "ws"),
            base_commit="a" * 40,
            branch="main",
            status="previewed",
            v1_supported=True,
            patch_checksum=checksum,
        )

        mock_ws = MagicMock()
        mock_ws.run_dir.return_value = run_dir
        mock_ws.read_run_json.return_value = meta

        git_state = MagicMock()
        git_state.is_clean = False

        mock_val_output = MagicMock()
        mock_val_output.overall_passed = True
        mock_val_output.model_dump_json.return_value = "{}"

        with (
            patch(
                "orchestrator.commands.apply.WorkspaceManager",
                return_value=mock_ws,
            ),
            patch("orchestrator.commands.apply.bootstrap_environment"),
            patch("orchestrator.commands.apply.TargetConfig.load"),
            patch("orchestrator.commands.apply.resolve_approved_by"),
            patch("orchestrator.schemas.experiment.verify_experiment_or_warn"),
            patch(
                "orchestrator.lifecycle.classify_lifecycle",
                return_value=PatchLifecycleState.VALID,
            ),
            patch("orchestrator.git.current_head", return_value="a" * 40),
            patch("orchestrator.git.current_branch", return_value="main"),
            patch("orchestrator.git.repository_state", return_value=git_state),
            patch(
                "orchestrator.git.stash_create_untracked",
                return_value="stash_sha_abc",
            ) as mock_stash,
            patch("orchestrator.git.rev_parse_tree", return_value="t" * 40),
            patch(
                "orchestrator.git.create_controlled_branch",
                return_value=MagicMock(return_code=0),
            ),
            patch(
                "orchestrator.git.apply_patch",
                return_value=MagicMock(return_code=0),
            ),
            patch(
                "orchestrator.agents.validator.run",
                return_value=(mock_val_output, None),
            ),
        ):
            apply_execute(
                run_id,
                allow_dirty=True,
                workspace=tmp_path / "ws",
            )

            mock_stash.assert_called_once()
