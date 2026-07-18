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
try_apply_dry_run → CONFLICT
  AND reverse-check PASSED
  AND HEAD == base_commit
  AND residue-free working tree        → ALREADY_APPLIED
try_apply_dry_run → CONFLICT           → CONFLICT
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.git import (
    get_current_head,
    head_tree_sha,
    try_apply_dry_run,
    try_apply_dry_run_reverse,
    working_tree_equals_expected_state,
)
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
        VALID, REBASEABLE, CONFLICT, ALREADY_APPLIED, or STALE.
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

    # --- CONFLICT → probe for ALREADY_APPLIED --------------------------------
    if apply_status is ApplyCheckStatus.CONFLICT:
        return _probe_already_applied(patch_path, target_path, base_commit)

    # apply_status is PASSED from here onward.
    head: str = get_current_head(target_path)

    if head == base_commit:
        return PatchLifecycleState.VALID

    return PatchLifecycleState.REBASEABLE


def _probe_already_applied(
    patch_path: Path,
    target_path: Path,
    base_commit: str,
) -> PatchLifecycleState:
    """Determine whether the CONFLICT is actually an ALREADY_APPLIED state.

    Three conditions must hold simultaneously:
    1. ``git apply --check --reverse`` passes (patch content is in the tree).
    2. HEAD == base_commit (HEAD has not advanced).
    3. The working tree matches baseline + patch with no extraneous changes.

    Only handles the clean-tree case (the initial run started from a clean
    working tree). Preserving pre-existing dirt across a resume is future
    scope -- see docs/context/plan-issue-258-resumable-apply.md (Part 3).
    """
    # Condition 1: reverse-check
    reverse = try_apply_dry_run_reverse(patch_path, target_path)
    if reverse is not ApplyCheckStatus.PASSED:
        return PatchLifecycleState.CONFLICT

    # Condition 2: HEAD stability
    head: str = get_current_head(target_path)
    if head != base_commit:
        return PatchLifecycleState.CONFLICT

    baseline_tree = head_tree_sha(target_path)
    if baseline_tree is None:
        return PatchLifecycleState.CONFLICT

    # Condition 3: residue-free working tree
    if not working_tree_equals_expected_state(patch_path, target_path, baseline_tree):
        return PatchLifecycleState.CONFLICT

    return PatchLifecycleState.ALREADY_APPLIED
