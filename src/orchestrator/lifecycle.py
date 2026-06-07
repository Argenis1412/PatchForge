"""Patch lifecycle classification for the V1 pipeline.

This module provides a single pure function, ``classify_lifecycle``, that
determines whether a patch stored in the run workspace is still compatible
with the current repository state.

Decision matrix
---------------
patch.diff missing or empty            → STALE
try_apply_dry_run → ERROR              → STALE
HEAD == base_commit AND PASSED         → VALID
HEAD != base_commit AND PASSED         → REBASEABLE
try_apply_dry_run → CONFLICT           → CONFLICT
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.git import get_current_head, try_apply_dry_run
from orchestrator.schemas.artifacts import PATCH_DIFF, PatchLifecycleState
from orchestrator.schemas.git import ApplyCheckStatus
from orchestrator.workspace import WorkspaceManager


def classify_lifecycle(run_id: str, workspace: WorkspaceManager) -> PatchLifecycleState:
    """Classify the lifecycle state of the patch for *run_id*.

    Args:
        run_id:    Identifier of an existing run (must have a valid run directory).
        workspace: WorkspaceManager pointing at the active workspace root.

    Returns:
        A :class:`PatchLifecycleState` value indicating whether the patch is
        VALID, REBASEABLE, CONFLICT, or STALE.
    """
    run_dir: Path = workspace.run_dir(run_id)
    patch_path: Path = run_dir / PATCH_DIFF

    # --- STALE: patch file missing or empty -----------------------------------
    if not patch_path.exists() or patch_path.stat().st_size == 0:
        return PatchLifecycleState.STALE

    # Read base_commit from the persisted run metadata.
    run_metadata = workspace.read_run_json(run_id)
    base_commit: str = run_metadata.base_commit
    target_path: Path = Path(run_metadata.target_path)

    # --- Dry-run git apply --check -------------------------------------------
    apply_status: ApplyCheckStatus = try_apply_dry_run(patch_path, target_path)

    # --- STALE: process-level error (git not found, invalid patch format) ----
    if apply_status is ApplyCheckStatus.ERROR:
        return PatchLifecycleState.STALE

    # --- CONFLICT: patch cannot be applied cleanly ---------------------------
    if apply_status is ApplyCheckStatus.CONFLICT:
        return PatchLifecycleState.CONFLICT

    # apply_status is PASSED from here onward.
    head: str = get_current_head(target_path)

    if head == base_commit:
        return PatchLifecycleState.VALID

    return PatchLifecycleState.REBASEABLE
