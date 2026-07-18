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

## Part 2 — Resume execution, clean case only (future issue)

**Goal:** `apply` actually resumes from `ALREADY_APPLIED` when the initial
run was on a clean tree (no `--allow-dirty`).

Scope:

- `_hydrate_apply_result_for_resume(run_dir)` — WAL hydration from
  `apply.json`, refusing to resume if `status != "applying"` or the backup
  diff pointer is missing/stale.
- Triple isolation verification before resuming: branch name, HEAD SHA, and
  residue-free tree (from Part 1's classifier) must all match the WAL.
  Zero remediation mutations on mismatch — abort only.
- Re-run the post-apply validator and route its outcome (pass/fail/**error**)
  through the same rollback path the happy-path already uses.
  **Must fix the validator-exception-swallowing bug from the review before
  merging this part** (see below) — an exception from the validator must
  never be treated as "nothing to roll back."
- `bootstrap_environment` + `TargetConfig.load` must run before the
  validator on the resume path too, but **must not reload `TargetConfig`
  from the mutated (patch-already-applied) tree** — see the config-reload
  bug below. Simplest correct approach: persist the pre-apply
  `TargetConfig` (it's a Pydantic model — `model_dump_json()` round-trips
  cleanly) to a run-dir snapshot file at the moment it's first loaded in
  the happy-path, before `git apply` runs; the resume path loads that
  snapshot instead of calling `TargetConfig.load()` again.
- Repo-lock acquisition ordering: acquire the lock **before** any
  isolation/lifecycle/branch/HEAD checks (including on the resume path),
  not after. A lock-acquisition failure means contention with another
  worker and must abort immediately.
- Checksum verification: must also run on the resume path (verify
  `patch.diff` still matches the checksum recorded at preview time) before
  trusting anything the WAL says.
- Tests: resume success, resume+validator-failure+rollback,
  split-brain (branch/HEAD mismatch) aborts, WAL-not-hydratable aborts,
  validator-exception-during-resume triggers rollback (not false success),
  config used by the resumed validator matches the pre-apply snapshot (not
  the patched tree).

## Part 3 — Dirt snapshot for `--allow-dirty` (future issue)

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

## Part 4 — Hardening / cleanup (future issue, may fold into Part 2/3)

Anything from the review not already covered above:

- Confirm the `core.filemode=false` fix from Part 1 doesn't mask real
  content differences (regression test).
- Regression test: real CONFLICT (genuinely different content) still
  produces the original CONFLICT message, unaffected by the new
  classification path.
- Revisit whether the resume path needs its own `--allow-dirty`-equivalent
  flag or CLI messaging once Parts 2-3 land together.

## Confirmed bugs found in review (do not lose these)

These were found reviewing the (now-reduced) implementation and are **not
present in Part 1** (the resume-execution and dirt-snapshot code they
apply to has been stripped from this branch), but must be fixed when
Parts 2/3 are (re)implemented:

1. `_run_post_apply_validation` converted validator exceptions to `None`,
   and both callers only checked `post_val_output is not None and not
   .overall_passed` before rolling back — meaning a validator that
   **crashed** (raised an exception) was silently treated as if it had
   never been asked to validate, and the apply flow fell through to
   `status="applied"`, `success=True`. This is the most severe finding;
   any Part 2 re-implementation must propagate validator exceptions as an
   explicit failure that routes through the rollback path.
2. `TargetConfig.load()` was called once in a shared prologue and reused
   for both the happy-path (tree at `base_commit`, correct) and the resume
   path (tree already has the uncommitted patch applied from the prior
   run) — meaning a patch that modifies `orchestrator.json` or files
   `detect_capabilities` inspects could alter its own post-apply
   validation config. Part 2 must snapshot the pre-apply config and reuse
   that snapshot on resume, never reloading from the mutated tree.
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
7. Repo-lock acquisition happened *after* isolation/lifecycle/branch/HEAD
   checks and git mutations began, instead of before — a race window
   between two workers. See Part 2.
8. Patch checksum verification was scoped to the happy-path only; the
   resume path never re-verified `patch.diff` against the checksum
   recorded at preview time. See Part 2.
9. `test_apply_aborts_if_head_changed` (test_hardening.py) used a
   syntactically-invalid dummy patch, so after the lifecycle-classification
   reorder it now exercises the STALE path instead of the intended
   HEAD-divergence path; the review suggested using a real, parseable
   patch and asserting `lifecycle_state == REBASEABLE` specifically. Applies
   only once Part 1's classification reorder is reintroduced into a version
   of `apply.py` that still has the old HEAD-mismatch check — revisit
   the test alongside whichever part restores that flow.

## Status

- Branch `fix/issue-258-apply-resume-already-applied` is being reduced to
  Part 1 scope only (this document was added in that same pass).
- Parts 2-4 need their own issues before implementation starts, each
  following the standard workflow (clarify → criteria → challenge → plan
  → adversarial review → approval → implement → diff review → tests → QA).
