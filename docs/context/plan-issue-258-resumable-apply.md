# Plan — Issue #258, split into 4 parts

## Why this was split

The original single-shot implementation on
`fix/issue-258-apply-resume-already-applied` grew `apply.py` to ~900 lines
across one PR (lifecycle detection + WAL resume + dirt-snapshot + locking
touch-ups), which made it too large to review with confidence. A follow-up
code review (bot) found several real, confirmed bugs in the unmerged
implementation — see **Confirmed bugs found in review** below. Rather than
patch all of them in the same oversized diff, the work is split into 4
sequential PRs, each independently reviewable and testable. The branch
`fix/issue-258-apply-resume-already-applied` is being reduced to **Part 1
only**; Parts 2-4 are separate future issues/PRs starting from a clean base.

## Part 1 — Lifecycle detection only (this branch, reduced scope)

**Goal:** `classify_lifecycle` correctly distinguishes "patch already applied
to working tree, matching HEAD" from a real merge conflict, and `apply`
surfaces a clear, accurate message for that state instead of the misleading
"CONFLICT... HEAD has diverged" message with identical SHAs. **No automatic
resume/execution is implemented in this part** — hitting `ALREADY_APPLIED`
still exits 1, just with a correct and helpful message.

Scope:

- `PatchLifecycleState.ALREADY_APPLIED` enum value
  (`schemas/artifacts.py`).
- `classify_lifecycle` / `_probe_already_applied` / `_resolve_baseline`
  (`lifecycle.py`) — reverse-check (`git apply --check --reverse`) + HEAD
  stability + residue-free working tree via a temporary Git index
  (`read-tree` baseline → `apply --cached` forward → `diff-files --quiet`
  with `core.filemode=false`). **Clean-tree case only** — no
  `--allow-dirty` / stash baseline support (that's Part 3).
- Git helpers (`git.py`): `try_apply_dry_run_reverse`, `head_tree_sha`,
  `rev_parse_tree`, `_list_untracked`, `working_tree_equals_expected_state`
  (simplified: no `stash_sha` parameter — always expects zero untracked
  residue).
- `apply.execute()`: when `classify_lifecycle` returns `ALREADY_APPLIED`,
  print a distinct, accurate error message (not the CONFLICT message) and
  exit 1. No WAL hydration, no resume, no validator re-run, no rollback
  changes.
- Tests: `test_lifecycle.py` ALREADY_APPLIED cases (already written and
  passing). Drop `test_apply_resumable.py`'s resume-execution and
  dirt-snapshot tests (those exercise Part 2/3 code that no longer exists
  in this branch); keep only lifecycle-classification-adjacent coverage if
  any remains applicable.

Non-goals for Part 1 (deferred): resuming execution, dirt snapshot,
untracked-file preservation, stash plumbing, lock reordering, config
reload from base commit, checksum-on-resume.

## Part 2 — Resume execution, clean case only ✅ DONE

Implemented on branch `fix/issue-260-resume-execution-part2`, tracked as
issue [#260](https://github.com/Argenis1412/PatchForge/issues/260) (opened
as its own issue rather than continuing under #258, per the split
rationale above).

**Goal:** `apply` actually resumes from `ALREADY_APPLIED` when the initial
run was on a clean tree (no `--allow-dirty`). Achieved.

Scope as implemented (`src/orchestrator/commands/apply.py`,
`src/orchestrator/schemas/artifacts.py`):

- `_hydrate_apply_result_for_resume(run_dir)` — WAL hydration from
  `apply.json`, refusing to resume if `status != "applying"`, the backup
  diff pointer is missing/stale, or the file is unparseable/corrupt.
- Triple isolation verification before resuming: **live current branch**
  (via `current_branch(target_path)`, not a locally re-derived constant —
  see the adversarial-review addendum below), HEAD SHA, and residue-free
  tree (from Part 1's classifier) must all match the WAL. Zero remediation
  mutations on mismatch — abort only.
- Re-run the post-apply validator and route its outcome (pass/fail/**error**)
  through the same rollback path the happy-path already uses, by sharing
  one validation/outcome code block between both paths (`if
  lifecycle_state is ALREADY_APPLIED: ... else: ...` then falls through to
  shared validation) — no duplicated rollback logic to keep in sync.
  Validator exceptions route through the same "treat `None` as failure"
  check the happy path already had, so the swallowing bug from the review
  is avoided by construction rather than by a new special case.
- `TargetConfig` snapshot: persisted to `target_config_snapshot.json` in
  the run dir via `model_dump_json()` **before** the WAL's first
  checkpoint write (not after — see addendum below), at the moment it's
  first loaded on the happy path. The resume path loads this snapshot via
  `model_validate_json()` and **never calls `TargetConfig.load()`** at all
  (moved entirely into the VALID-only branch — see addendum).
- Repo-lock acquisition ordering: lock is acquired before the *first* HEAD
  read (the "HEAD changed since preview" gate), lifecycle classification,
  the `is_clean` check, and `TargetConfig.load()` -- i.e. before any git
  read at all, not just before lifecycle classification. `is_clean` itself
  is computed via `repository_state()`, which now lives entirely inside
  the VALID-only branch (ALREADY_APPLIED never calls it, since its dirty
  tree is expected and irrelevant to that path). A lock-acquisition
  failure aborts immediately instead of being silently ignored (the old
  code never checked `acquire_repo_lock`'s return value).
- Checksum verification: unchanged in position (runs once, before the
  ALREADY_APPLIED/VALID branch split), so it now covers the resume path
  simply because the resume path no longer exits before reaching it.
- Tests (`tests/test_apply_resumable.py`, 19 cases): resume success, resume
  bypasses `is_clean`, resume+validator-failure+rollback,
  resume+validator-exception+rollback, WAL-not-hydratable aborts (missing
  file, wrong status, corrupt JSON), backup-pointer aborts (missing,
  stale-path-but-file-deleted), branch/HEAD mismatch aborts, config
  snapshot missing/corrupt aborts, config used by the resumed validator
  matches the pre-apply snapshot (not the patched tree, and
  `TargetConfig.load` is asserted never called on resume), happy-path
  snapshot is written before `git apply`, lock-contention aborts before
  lifecycle classification, lock is released via `finally` on
  CONFLICT/STALE/REBASEABLE, and — the highest-risk regression check —
  `VALID` + dirty tree without `--allow-dirty` still blocks (proving the
  `is_clean` relocation didn't invert or drop the gate).

**Addendum — bugs found and fixed during two rounds of adversarial plan
review, beyond what the original Part 2 scope above anticipated:**

- The `is_clean` gate and the `TargetConfig.load()` call both originally
  ran *before* `classify_lifecycle`, in the shared prologue. Since
  ALREADY_APPLIED's working tree is dirty by definition (the uncommitted
  patch is the dirt) and a patch can leave `orchestrator.json` transiently
  invalid, either check running unconditionally would have made the
  resume path unreachable 100% of the time. Both were moved into the
  VALID-only branch.
- The branch-isolation check was initially specified as `branch_name ==
  WAL.branch` — but both sides are computed by the same deterministic
  formula from `run_id`/`issue_number`, so that comparison can never fail.
  Replaced with `current_branch(target_path) == WAL.branch` (live git
  state vs. what the original run recorded), which actually catches a
  user switching branches between the crash and the resume attempt.
- The config snapshot must be written **before** the WAL's first
  checkpoint, not after: writing WAL-then-snapshot leaves a crash window
  where the WAL is hydratable but the snapshot is missing — a
  resumable-looking state the resume path could never actually complete.
  Snapshot-then-WAL means a crash between the two writes instead leaves
  the snapshot present and the new WAL absent (or still showing the
  previous run's state); a missing or non-`"applying"` WAL fails
  `_hydrate_apply_result_for_resume`'s checks, so no resume is attempted
  and the orphaned snapshot is harmless.
- `acquire_repo_lock`'s return value was previously ignored entirely; a
  `False` return (lock contention) now aborts immediately instead of
  proceeding without the lock.

## Part 3 — Dirt snapshot for `--allow-dirty` (issue #262, ✅ DONE)

Implemented on branch `feat/issue-262-dirt-snapshot-allow-dirty`. Camino A:
capture + restore for the initial `--allow-dirty` run only; does not touch
`classify_lifecycle` or the resume path (that remains Part 4). Full
`/clarify` → `/challenge-ac` → design → `/adversarial` cycle run before
implementation; see addendum below for what changed as a result.

### Addendum: adversarial review findings and resolutions

Five issues were found reviewing the initial design, before any code was
written:

1. **Submodule detection was a no-op.** The original design proposed
   `git diff --submodule=short --quiet` to detect dirty submodules —
   `--quiet` suppresses all stdout, making any check based on its output
   permanently false. Fixed: use `git submodule status`, whose porcelain
   output is not suppressed; a `+`/`-`/`U` prefix on any line means dirty.
2. **Crash-window data loss was undocumented.** If the process crashes
   between capturing dirt and finishing the apply, the dirt sits in a
   `refs/stash` entry the user doesn't know about. Mitigated (not solved —
   this is inherent to Camino A) with a startup advisory: `apply` now
   checks for orphaned `patchforge:*` stash entries and warns with the
   recovery command, cross-referencing against known `run.json` files to
   avoid false positives from a stash a user named manually.
3. **The manual 3-parent stash commit relies on undocumented git
   internals**, so a mandatory structure test
   (`test_stash_structure_valid`) asserts the parent count and that
   `git stash apply --index` accepts the result — this will fail loudly in
   CI if git's internal handling of `stash apply` ever changes, rather
   than silently corrupting user data.
4. **O(n) subprocess calls for untracked files** (`git update-index --add`
   per file) would not scale and risked hitting the 30s timeout on repos
   with many untracked files. Fixed: `git update-index --add --stdin` in a
   single call.
5. **The Part 3 / Part 4 contract was undefined**, and the original design
   would have silently lost dirt in a *normal* resume flow (not just a
   crash edge case): if `--allow-dirty` captured dirt, then apply crashed
   during post-apply validation, an automatic resume (Part 2) would
   validate and roll back the *patch* correctly but never knew to restore
   the dirt stash. Fixed: the ALREADY_APPLIED/resume branch now aborts
   explicitly with a recovery message whenever `run_metadata.dirt_stash_sha`
   is set, instead of proceeding silently. This is the contract Part 4 must
   honor or replace.

### Implementation-time correction: dirt must also be restored on success

The original issue's acceptance criteria only described restoring dirt
"on rollback." Implementing it strictly that way would have been a
regression: pre-Part-3, `--allow-dirty` applied the patch directly onto
the dirty tree (patch changes and pre-existing dirt coexisted,
uncommitted). Part 3's capture step cleans the tree before applying the
patch, so if dirt were *only* restored on rollback, a **successful** apply
would silently strand the user's original dirt in a stash forever. Fixed:
on success, the captured dirt is also restored on top of the applied
patch (`stash apply --index`), matching pre-Part-3 semantics. If that
restore conflicts with the patch's own changes, the patch stays applied
(it already passed validation) and the user is pointed at the stash to
resolve the conflict manually — this is not rolled back, since rolling
back a validated patch because of an unrelated stash conflict would be
worse.

### Bug found during implementation: root-commit parent resolution

The "combine tracked + untracked" step originally reused `HEAD` directly
as the stash's "index" parent when there was no tracked dirt (untracked
files only). `git stash apply` internally diffs `parent2` against
`parent2^` to compute the tracked-changes portion, so `parent2` must
itself have a resolvable parent — reusing `HEAD` directly breaks this
whenever `HEAD` is a root commit (no parent of its own), producing
`fatal: ambiguous argument '<sha>^2^'`. Fixed by creating a synthetic
no-op "index" commit (`HEAD`'s own tree, `HEAD` as its parent) so
`parent2^` always resolves to `HEAD` regardless of `HEAD`'s own ancestry.
Caught by `test_allow_dirty_untracked_only_captures_and_restores`.

<details>
<summary>Original Part 3 design (pre-implementation, kept for history)</summary>


**Goal:** when the *original* (first, non-resumed) `apply --allow-dirty`
run starts from a dirty tree, that pre-existing user dirt (including
untracked files) is captured before any mutation, and restored on rollback
— without ever mutating the working tree during the snapshot itself.

**Critical fix required, confirmed by empirical testing during review:**
`git stash create --include-untracked` **silently ignores the
`--include-untracked`/`-u` flag** (`git stash create` only accepts an
optional message argument; unknown git versions may error, this one
silently no-ops). The resulting stash commit has only 2 parents (HEAD +
index), never the 3rd "untracked files" parent the rest of the design
depends on. This was verified locally:

```
$ git stash create --include-untracked   # in a repo with an untracked file
d1be46c1eaa2652a9b144f5c66c53e6c9efa87d0
$ git rev-parse d1be46c1eaa2652a9b144f5c66c53e6c9efa87d0^3
fatal: ambiguous argument '...^3': unknown revision or path not in the working tree.
```

The entire "capture untracked dirt without mutating the tree" feature
must be rebuilt using low-level plumbing, not `stash create -u`. A working
recipe (verified locally, working tree confirmed untouched throughout):

1. `tracked_sha = git stash create` (2-parent stash of tracked
   modifications only; empty output if no tracked modifications exist).
2. List untracked non-ignored files (`_list_untracked`, already exists).
3. If none: return `tracked_sha` as-is (2-parent, no 3rd parent needed).
4. If some exist: build a tree for just those files using a **scratch**
   `GIT_INDEX_FILE` (never the repo's real index) —
   `GIT_INDEX_FILE=<scratch> git update-index --add -- <files>` then
   `GIT_INDEX_FILE=<scratch> git write-tree` → `untracked_tree`.
5. `untracked_commit = git commit-tree <untracked_tree> -m "..."` (no
   parents).
6. If `tracked_sha` exists: combine —
   `git commit-tree <tracked_sha>^{tree} -p <tracked_sha>^1 -p
   <tracked_sha>^2 -p <untracked_commit> -m "..."` → final 3-parent SHA,
   same shape `git stash push -u` would produce.
   If `tracked_sha` is empty (only untracked dirt, no tracked
   modifications): use `HEAD` for both parent 1 and parent 2 instead of
   `tracked_sha^1`/`^2`, and `HEAD^{tree}` for the tree.
7. `git stash apply --index <combined_sha>` restores both tracked
   modifications and untracked files correctly (verified locally).

If snapshot creation fails or is incomplete at any step, **abort the apply
instead of proceeding** — do not silently fall back to "no dirt captured"
when dirt is known to exist, since that would silently discard user files
on a later rollback's `git clean -fd`.

Additional fixes required in this part, confirmed by review:

- `_list_stash_untracked` must return `None` (fail closed) when the
  third-parent lookup fails for a reason *other than* "this stash
  legitimately has no 3rd parent" (i.e. a stash created via step 3 above,
  tracked-only, no untracked dirt at snapshot time). Distinguish the two
  by checking the stash commit's actual parent count
  (`git rev-list --parents -n 1 <sha>`) before attempting `ls-tree
  <sha>^3` — parent count < 3 is the legitimate empty case; parent count
  >= 3 with a failing `ls-tree` is a real error and must return `None`,
  not an empty set (an empty set is currently indistinguishable from
  "verified no untracked residue", which is unsafe).
- The residue check must compare untracked file **contents** (blob SHAs
  via `git ls-tree` + `git hash-object`), not just **paths**. An edited
  untracked file (same filename, different content, between the initial
  crashed run and a resume attempt) currently passes the path-set
  comparison undetected, and would be silently destroyed by
  `git clean -fd` on a subsequent rollback.
- `_rollback_with_stash` must not report `rolled_back=True` /
  `rollback_head=<sha>` when the code-rollback (`git reset --hard` +
  `git apply --reverse`) succeeded but the dirt-stash restore
  (`git stash apply --index`) failed. Currently the function returns
  `True` unconditionally once the code-rollback step succeeds, silently
  discarding the fact that the user's original dirt was not restored. The
  existing warning message (pointing at the dangling stash SHA for manual
  recovery) should stay, but the function's return value — and therefore
  everything downstream that persists `rolled_back`/`rollback_head` to the
  WAL and `run.json` — must reflect the incomplete outcome.

</details>

## Part 3.5 — Dirt-storage hardening (issue #264, ✅ DONE)

Discovered as a prerequisite during Part 4's planning-stage adversarial
review, not part of the original 4-part split: reusing Part 3's
`refs/stash`-based storage for the resume case (Part 4) would route a
high-risk, unbounded-time-gap call path through a positional
(`stash@{0}`) addressing scheme that the codebase's own
`check_orphaned_dirt_stash` docstring already flagged as fragile ("the
ref's index can shift as other stash operations happen"). A first
attempted fix (resolve the SHA's current `stash@{N}` position immediately
before dropping) still left a real TOCTOU gap between "resolve" and
"drop" as two separate subprocess calls. Root cause: using `refs/stash` (a
shared, globally-mutable, user-visible structure) as PatchForge's private
transactional storage at all, not just the specific addressing bug.

**Implemented:** dirt commits are now anchored under a private per-run ref,
`refs/patchforge/dirt/{run_id}`, via `git update-ref`/`git update-ref -d`
(`store_dirt_ref`/`delete_dirt_ref`/`check_orphaned_dirt_refs`/
`dirt_ref_name` in `git.py`) instead of `git stash store`/`stash@{N}`
(`stash_create_dirt`/`stash_apply_dirt` themselves are unchanged — the
commit-building and restore mechanism were never the problem, only the
anchor/cleanup addressing). Addressed by exact ref name instead of stack
position, so no TOCTOU window exists at any point. `store_dirt_ref` is
create-only (fails rather than silently overwriting a stale ref left by an
incomplete prior cleanup); `delete_dirt_ref` failure after a successful
restore is treated as non-fatal (the working tree is already correct by
that point — surfaced as a warning, left for the orphan advisory to catch
later) rather than mirroring Part 3's FATAL-on-apply-failure pattern,
which doesn't apply here.

**Orphan-advisory simplified, not just ported:** `refs/patchforge/dirt/*`
is exclusively PatchForge's own namespace, so the old
name-collision-suppression logic (needed because `refs/stash` is shared
with the user's own `git stash` workflow) no longer has a scenario to
guard against and was removed rather than ported. Every orphan found is
now reported directly by `run_id` (read straight from the ref name, no
reflog-message grepping or whole-run.json-directory scan needed), with an
age-based (7 days, via `run.json` mtime — not `run_id`'s embedded
timestamp, whose format isn't consistent across the codebase's several
run-ID generators) manual-cleanup hint.

**Also fixed while this code was already being touched:**
`stash_apply_dirt`'s `returncode != 0` didn't distinguish a clean no-op
failure from a partial 3-way-merge that left conflict markers in the tree
(`git stash apply --index` has real merge semantics, not simple
all-or-nothing apply) — a pre-existing Part 3 ambiguity in the
already-merged happy-path success-restore case. New `has_merge_conflicts()`
in `git.py` (checks `git status --porcelain=v1` for unmerged-path codes)
now distinguishes the two in the FATAL/warning messaging at all four
restore-failure call sites.

**Known accepted limitation, not solved:** anchoring dirt captures via
*any* git ref — including `refs/stash`, which this replaces — is swept up
by `git push --mirror` and equivalent wildcard-refspec pushes. The private
per-run namespace changes this risk's *shape* for the worse, not better:
today's design has at most one ref (`refs/stash`'s tip) exposed at any
moment; `refs/patchforge/dirt/{run_id}` lets orphaned captures from
multiple past crashed runs accumulate and all be swept into a single
`--mirror` push simultaneously. Mitigated, not eliminated, by the
age-based cleanup hint above. Two other storage directions were considered
and rejected during this issue's `/clarify`: plain-file storage in the
existing per-run workspace directory (eliminates the leak vector
entirely, but requires reimplementing the tracked+untracked restore logic
without git's native 3-way-merge semantics — a much larger, riskier
rewrite) and keeping `refs/stash` with only the TOCTOU gap narrowed (still
doesn't fully close it, per the analysis above).

**Part 3/4 contract, updated (was Part 3/4, now effectively Part
3.5/4):** `RunMetadata.dirt_stash_sha` keeps its field name and still
holds a commit SHA — Part 4 (not yet implemented) can consume it exactly
as originally planned; only the *anchoring* mechanism changed, not the
field's meaning or the value it holds.

Tests: `tests/test_apply_resumable.py`, 4 new
(`test_capture_aborts_if_dirt_ref_already_exists`,
`test_dirt_ref_namespace_isolated_from_user_stash` — proves namespace
isolation from `refs/stash` directly rather than via race-timing,
`test_orphaned_dirt_ref_shows_age_cleanup_hint`,
`test_dirt_restore_succeeds_even_if_ref_delete_fails`), 3 renamed to match
the new mechanism (`stash_store_ref`→`store_dirt_ref`,
`stash_drop`→`delete_dirt_ref` throughout), 1 removed
(`test_orphaned_stash_warning_suppressed_on_name_collision` — its
name-collision scenario is no longer reachable once the namespace is
exclusively PatchForge's own).

## Part 4 — Hardening / cleanup (future issue, may fold into Part 2/3)

Anything from the review not already covered above:

- Confirm the `core.filemode=false` fix from Part 1 doesn't mask real
  content differences (regression test).
- Regression test: real CONFLICT (genuinely different content) still
  produces the original CONFLICT message, unaffected by the new
  classification path.
- **Part 3/4 contract (defined during Part 3 implementation):** Part 3
  persists `RunMetadata.dirt_stash_sha` and aborts automatic resume
  whenever it is set, rather than proceeding silently. Part 4's job is to
  either (a) teach `classify_lifecycle`/the resume path to restore the
  dirt stash as part of a full resume, replacing the abort with real
  resume support, or (b) formally accept the abort as permanent behavior
  and turn its error message into fully self-service manual recovery
  instructions. Whichever is chosen, do not silently drop the guard
  without replacing it — the guard exists because a silent resume with
  unrestored dirt is a real data-loss path in the normal (non-crash) flow,
  confirmed during Part 3's adversarial review.
- The crash-window gap acknowledged in Part 3 (dirt captured, process dies
  before the apply finishes, leaving a `refs/stash` entry the user has to
  notice via the startup advisory) is inherent to Camino A and is in scope
  for Part 4 if a stronger guarantee is wanted (e.g. `git worktree add`
  instead of resetting the main tree, discussed and deferred during Part 3
  adversarial review as a larger architectural change).

## Confirmed bugs found in review (do not lose these)

These were found reviewing the (now-reduced) implementation and are **not
present in Part 1** (the resume-execution and dirt-snapshot code they
apply to has been stripped from this branch), but must be fixed when
Parts 2/3 are (re)implemented:

1. **Fixed in Part 2.** `_run_post_apply_validation` converted validator
   exceptions to `None`, and both callers only checked `post_val_output is
   not None and not .overall_passed` before rolling back — meaning a
   validator that **crashed** (raised an exception) was silently treated
   as if it had never been asked to validate, and the apply flow fell
   through to `status="applied"`, `success=True`. Fixed by construction:
   the resume path shares the exact same validation/outcome code block as
   the happy path, so there is only one "treat `None` as failure" check to
   keep correct, not two to keep in sync. Regression test:
   `test_resume_validator_exception_rollback`.
2. **Fixed in Part 2.** `TargetConfig.load()` was called once in a shared
   prologue and reused for both the happy-path (tree at `base_commit`,
   correct) and the resume path (tree already has the uncommitted patch
   applied from the prior run) — meaning a patch that modifies
   `orchestrator.json` or files `detect_capabilities` inspects could alter
   its own post-apply validation config. Fixed by snapshotting the
   pre-apply config to `target_config_snapshot.json` and moving
   `TargetConfig.load()` entirely into the VALID-only branch — the resume
   path never calls it. Regression tests: `test_resume_uses_config_snapshot`,
   `test_resume_never_calls_target_config_load`.
3. `git stash create --include-untracked` silently no-ops (confirmed
   empirically) — the entire dirt-snapshot feature from the original
   implementation never actually captured untracked files. See Part 3's
   plumbing recipe.
4. `_list_stash_untracked` returned `set()` (not `None`) on any lookup
   failure, indistinguishable from "verified empty" — see Part 3.
5. Untracked file comparison only checked path sets, not content — an
   edited untracked file between crash and resume went undetected. See
   Part 3.
6. `_rollback_with_stash` returned `True` even when the dirt-stash restore
   step failed, misreporting a partial rollback as complete. See Part 3.
7. **Fixed in Part 2.** Repo-lock acquisition happened *after*
   isolation/lifecycle/branch/HEAD checks and git mutations began, instead
   of before — a race window between two workers. Also, the lock's return
   value was never checked (contention was silently ignored). Both fixed:
   lock is now acquired before the very first HEAD read (the
   "HEAD changed since preview" gate) and before lifecycle classification,
   and a `False` return aborts immediately. Regression tests:
   `test_resume_aborts_lock_contention`, `test_lock_released_on_conflict`.
8. **Fixed in Part 2.** Patch checksum verification was scoped to the
   happy-path only; the resume path never re-verified `patch.diff` against
   the checksum recorded at preview time. Fixed: checksum verification now
   runs once, before the ALREADY_APPLIED/VALID split, so both paths flow
   through it.
9. `test_apply_aborts_if_head_changed` (test_hardening.py) used a
   syntactically-invalid dummy patch, so after the lifecycle-classification
   reorder it now exercises the STALE path instead of the intended
   HEAD-divergence path; the review suggested using a real, parseable
   patch and asserting `lifecycle_state == REBASEABLE` specifically. Applies
   only once Part 1's classification reorder is reintroduced into a version
   of `apply.py` that still has the old HEAD-mismatch check — revisit
   the test alongside whichever part restores that flow.

## Status

- Part 1 merged to `main` via PR
  [#259](https://github.com/Argenis1412/PatchForge/pull/259) (issue
  [#258](https://github.com/Argenis1412/PatchForge/issues/258)).
- Part 2 merged to `main` via PR
  [#261](https://github.com/Argenis1412/PatchForge/pull/261) (issue
  [#260](https://github.com/Argenis1412/PatchForge/issues/260)). Confirmed
  bugs 1, 2, 7, and 8 above are fixed as part of this work.
- Part 3 merged to `main` via PR
  [#263](https://github.com/Argenis1412/PatchForge/pull/263) (issue
  [#262](https://github.com/Argenis1412/PatchForge/issues/262)). Confirmed
  bugs 3, 4, 5, and 6 above are fixed as part of this work.
- Part 3.5 (prerequisite for Part 4, not part of the original 4-part split
  — see above) implemented on branch
  `fix/issue-264-dirt-storage-hardening`, tracked as issue
  [#264](https://github.com/Argenis1412/PatchForge/issues/264); QA green
  (`ruff check .`, `ruff format --check .`, `pytest` — 933 passed, 5
  skipped), not yet committed/pushed/PR'd.
- Part 4 not yet started (blocked on Part 3.5 merging first); a detailed
  plan for it — including a five-round adversarial-review trail that
  rejected three prior designs (a two-write WAL scheme, a dirt-aware
  `classify_lifecycle` extension) in favor of a smaller message-only
  mitigation — lives in
  `C:\Users\Visitante\.claude\plans\continuando-el-trabajo-de-sparkling-dragonfly.md`
  pending its own issue.
