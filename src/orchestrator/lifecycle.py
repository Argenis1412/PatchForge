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

import json
from pathlib import Path

from orchestrator.git import (
    get_current_head,
    head_tree_sha,
    rev_parse_tree,
    try_apply_dry_run,
    try_apply_dry_run_reverse,
    working_tree_equals_expected_state,
)
from orchestrator.schemas.artifacts import APPLY_JSON, PATCH_DIFF, PatchLifecycleState
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
        return _probe_already_applied(patch_path, target_path, base_commit, run_dir)

    # apply_status is PASSED from here onward.
    head: str = get_current_head(target_path)

    if head == base_commit:
        return PatchLifecycleState.VALID

    return PatchLifecycleState.REBASEABLE


def _probe_already_applied(
    patch_path: Path,
    target_path: Path,
    base_commit: str,
    run_dir: Path,
) -> PatchLifecycleState:
    """Determine whether the CONFLICT is actually an ALREADY_APPLIED state.

    Three conditions must hold simultaneously:
    1. ``git apply --check --reverse`` passes (patch content is in the tree).
    2. HEAD == base_commit (HEAD has not advanced).
    3. The working tree matches baseline + patch with no extraneous changes.
    """
    # Condition 1: reverse-check
    reverse = try_apply_dry_run_reverse(patch_path, target_path)
    if reverse is not ApplyCheckStatus.PASSED:
        return PatchLifecycleState.CONFLICT

    # Condition 2: HEAD stability
    head: str = get_current_head(target_path)
    if head != base_commit:
        return PatchLifecycleState.CONFLICT

    # Determine baseline tree and stash SHA from WAL (if available)
    baseline_tree, stash_sha = _resolve_baseline(target_path, run_dir)
    if baseline_tree is None:
        return PatchLifecycleState.CONFLICT

    # Condition 3: residue-free working tree
    if not working_tree_equals_expected_state(
        patch_path, target_path, baseline_tree, stash_sha=stash_sha
    ):
        return PatchLifecycleState.CONFLICT

    return PatchLifecycleState.ALREADY_APPLIED


def _resolve_baseline(target_path: Path, run_dir: Path) -> tuple[str | None, str | None]:
    """Resolve the expected baseline tree SHA and stash SHA from the WAL.

    Returns (baseline_tree_sha, stash_sha).  If the WAL has a
    ``pre_apply_dirty_stash_tree``, that is the baseline; otherwise HEAD's
    tree is used.  Returns (None, None) on any error.
    """
    stash_sha: str | None = None
    stash_tree: str | None = None

    apply_json = run_dir / APPLY_JSON
    if apply_json.exists():
        try:
            data = json.loads(apply_json.read_text(encoding="utf-8"))
            stash_sha = data.get("pre_apply_dirty_stash")
            stash_tree = data.get("pre_apply_dirty_stash_tree")
        except Exception:
            pass

    if stash_tree:
        return stash_tree, stash_sha

    tree = head_tree_sha(target_path)
    if tree is None:
        tree = rev_parse_tree(target_path, "HEAD")
    return tree, None
