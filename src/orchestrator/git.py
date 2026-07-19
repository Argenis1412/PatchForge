from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from orchestrator.schemas.git import (
    ApplyCheckStatus,
    GitCommandResult,
    RepositoryState,
    WorkingTreeStatus,
)


def is_git_repo(path: Path) -> bool:
    try:
        res = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return res.returncode == 0
    except FileNotFoundError as e:
        raise FileNotFoundError("Git executable not found in PATH") from e


def resolve_git_root(path: Path) -> Path:
    path = Path(path).resolve()
    try:
        res = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return Path(res.stdout.strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return path


def current_branch(repo_root: Path) -> str:
    """Return the name of the currently checked-out branch.

    Raises RuntimeError if git fails or the repo is in a detached HEAD state.
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to determine current branch in '{repo_root}': {e.stderr.strip()}"
        ) from e
    except FileNotFoundError as e:
        raise RuntimeError("Git executable not found in PATH") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"Command timed out while determining current branch in '{repo_root}'"
        ) from e


def current_head(repo_root: Path) -> str:
    """Return the full SHA of the current HEAD commit.

    Raises RuntimeError if git fails (e.g. empty repo, not a git dir, no git binary).
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to resolve HEAD in '{repo_root}': {e.stderr.strip()}") from e
    except FileNotFoundError as e:
        raise RuntimeError("Git executable not found in PATH") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Command timed out while resolving HEAD in '{repo_root}'") from e


def is_working_tree_clean(repo_root: Path) -> bool:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return res.stdout.strip() == ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def working_tree_status(repo_root: Path) -> WorkingTreeStatus:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        porcelain = res.stdout
        return WorkingTreeStatus(is_clean=porcelain.strip() == "", porcelain=porcelain)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        porcelain = getattr(e, "stderr", None) or str(e)
        return WorkingTreeStatus(is_clean=False, porcelain=porcelain)


def repository_state(repo_root: Path) -> RepositoryState:
    if not is_git_repo(repo_root):
        raise ValueError(f"Not a Git repository: {repo_root}")

    root = resolve_git_root(repo_root)
    head = current_head(root)
    branch = current_branch(root)
    is_clean = is_working_tree_clean(root)

    return RepositoryState(
        root=root,
        head=head,
        branch=branch,
        is_clean=is_clean,
    )


def check_patch(repo_root: Path, patch_path: Path) -> GitCommandResult:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "apply", "--check", str(patch_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return GitCommandResult(return_code=res.returncode, stdout=res.stdout, stderr=res.stderr)
    except FileNotFoundError as e:
        return GitCommandResult(return_code=127, stdout="", stderr=f"git executable not found: {e}")


def get_current_head(repo_path: Path) -> str:
    """Return the full SHA of the current HEAD commit in *repo_path*.

    Raises RuntimeError if git fails (e.g. empty repo, not a git dir, no git binary).
    """
    return current_head(repo_path)


def try_apply_dry_run(patch_path: Path, repo_path: Path) -> ApplyCheckStatus:
    """Run ``git apply --check`` against *patch_path* inside *repo_path*.

    Returns:
        ApplyCheckStatus.PASSED   -- rc == 0; patch applies cleanly.
        ApplyCheckStatus.CONFLICT -- git ran but rc != 0 (merge conflict).
        ApplyCheckStatus.ERROR    -- git executable not found or the process
                                     raised an unexpected OS-level error.
    """
    try:
        res = subprocess.run(
            ["git", "apply", "--check", str(patch_path)],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=30,
        )
        if res.returncode == 0:
            return ApplyCheckStatus.PASSED
        return ApplyCheckStatus.CONFLICT
    except FileNotFoundError:
        return ApplyCheckStatus.ERROR
    except Exception:
        return ApplyCheckStatus.ERROR


def create_controlled_branch(repo_root: Path, branch_name: str) -> GitCommandResult:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "checkout", "-b", branch_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return GitCommandResult(return_code=res.returncode, stdout=res.stdout, stderr=res.stderr)
    except FileNotFoundError as e:
        return GitCommandResult(return_code=127, stdout="", stderr=f"git executable not found: {e}")


def apply_patch(repo_root: Path, patch_path: Path) -> GitCommandResult:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "apply", str(patch_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return GitCommandResult(return_code=res.returncode, stdout=res.stdout, stderr=res.stderr)
    except FileNotFoundError as e:
        return GitCommandResult(return_code=127, stdout="", stderr=f"git executable not found: {e}")


def force_reset_apply(repo_root: Path, target_sha: str) -> GitCommandResult:
    try:
        res1 = subprocess.run(
            ["git", "-C", str(repo_root), "reset", "--hard", target_sha],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res1.returncode != 0:
            return GitCommandResult(
                return_code=res1.returncode,
                stdout=res1.stdout,
                stderr=res1.stderr,
            )
        res2 = subprocess.run(
            ["git", "-C", str(repo_root), "clean", "-fd"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return GitCommandResult(
            return_code=res2.returncode,
            stdout=res1.stdout + res2.stdout,
            stderr=res1.stderr + res2.stderr,
        )
    except FileNotFoundError as e:
        return GitCommandResult(return_code=127, stdout="", stderr=f"git executable not found: {e}")


def revert_apply(repo_root: Path) -> GitCommandResult:
    try:
        res1 = subprocess.run(
            ["git", "-C", str(repo_root), "checkout", "."],
            capture_output=True,
            text=True,
            timeout=30,
        )
        res2 = subprocess.run(
            ["git", "-C", str(repo_root), "clean", "-fd"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        rc = res1.returncode if res1.returncode != 0 else res2.returncode
        return GitCommandResult(
            return_code=rc,
            stdout=res1.stdout + res2.stdout,
            stderr=res1.stderr + res2.stderr,
        )
    except FileNotFoundError as e:
        return GitCommandResult(return_code=127, stdout="", stderr=f"git executable not found: {e}")


def git_push(repo_root: Path, branch: str, remote: str = "origin") -> GitCommandResult:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "push", "-u", remote, branch],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return GitCommandResult(return_code=res.returncode, stdout=res.stdout, stderr=res.stderr)
    except FileNotFoundError as e:
        return GitCommandResult(return_code=127, stdout="", stderr=f"git executable not found: {e}")


def push_delete_remote(repo_root: Path, branch: str, remote: str = "origin") -> GitCommandResult:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "push", remote, "--delete", branch],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return GitCommandResult(return_code=res.returncode, stdout=res.stdout, stderr=res.stderr)
    except FileNotFoundError as e:
        return GitCommandResult(return_code=127, stdout="", stderr=f"git executable not found: {e}")


def delete_local_branch(repo_root: Path, branch: str, force: bool = True) -> GitCommandResult:
    flag = "-D" if force else "-d"
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "branch", flag, branch],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return GitCommandResult(return_code=res.returncode, stdout=res.stdout, stderr=res.stderr)
    except FileNotFoundError as e:
        return GitCommandResult(return_code=127, stdout="", stderr=f"git executable not found: {e}")


def normalize_git_url(url: str) -> str:
    """Normalize a git remote URL or directory path to a standardized representation.

    Standardizes SCP-like syntax (git@github.com:org/repo.git) and HTTPS URLs
    by converting protocols, stripping usernames, standardizing slashes, and
    removing trailing '.git'.
    """
    url = url.strip()
    if not url:
        return ""

    # Parse ssh://git@host:port/path or ssh://git@host/path
    ssh_scheme_match = re.match(r"^ssh://(?:git@)?([^:/]+)(?::\d+)?/(.+)$", url, re.IGNORECASE)
    # Parse scp-like: git@host:path (e.g. git@github.com:org/repo)
    scp_match = re.match(r"^git@([^:]+):(.+)$", url, re.IGNORECASE)
    # Parse http(s)://[user@]host[:port]/path — capture scheme separately to preserve it
    http_match = re.match(r"^(https?://)(?:[^@]+@)?([^:/]+)(?::\d+)?/(.+)$", url, re.IGNORECASE)

    if ssh_scheme_match:
        host, path = ssh_scheme_match.groups()
        url = f"https://{host}/{path}"
    elif scp_match:
        host, path = scp_match.groups()
        url = f"https://{host}/{path}"
    elif http_match:
        scheme, host, path = http_match.groups()
        url = f"{scheme}{host}/{path}"

    # Standardize remaining string (casing and slashes)
    url = url.replace("\\", "/")
    # Remove duplicate slashes except after http/https protocol
    proto_match = re.match(r"^(https?://)", url, re.IGNORECASE)
    if proto_match:
        proto = proto_match.group(1)
        rest = url[len(proto) :]
        rest = re.sub(r"/+", "/", rest)
        url = proto + rest
    else:
        url = re.sub(r"/+", "/", url)

    # Strip trailing slashes
    if url.endswith("/"):
        url = url[:-1]

    # Strip trailing .git (case-insensitive)
    if url.lower().endswith(".git"):
        url = url[:-4]

    # If it is a local path or doesn't start with http/https, try resolving as absolute path
    if not url.lower().startswith("http://") and not url.lower().startswith("https://"):
        try:
            p = Path(url)
            # Resolve to absolute posix path
            resolved = str(p.resolve().as_posix())
            import sys

            if sys.platform.startswith("win"):
                return resolved.lower()
            return resolved
        except Exception:
            pass

    return url.lower()


def _git_config_get(repo_root: Path, key: str) -> str | None:
    """Return ``git config --get <key>`` output, or None on any failure.

    Graceful degradation for unset keys, timeouts, missing git binary, and
    any other OSError (permission denied, etc.) — provenance capture must
    never crash the calling command. Matches ``repository_identity()``'s
    broad ``except Exception`` posture for the same reason.
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--get", key],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0:
            return None
        value = res.stdout.strip()
        return value or None
    except Exception:
        return None


def git_config_user_name(repo_root: Path) -> str | None:
    """Return ``git config user.name``, or None if unset/unavailable."""
    return _git_config_get(repo_root, "user.name")


def git_config_user_email(repo_root: Path) -> str | None:
    """Return ``git config user.email``, or None if unset/unavailable."""
    return _git_config_get(repo_root, "user.email")


def repository_identity(repo_root: Path) -> str:
    """Return the repository's identity.

    Tries to retrieve the remote origin URL. If that's not available or fails,
    returns the absolute local path resolved as a posix path string.
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return str(Path(repo_root).resolve().as_posix())


# ---------------------------------------------------------------------------
# Helpers for apply lifecycle detection (issue #258, Part 1 — detection only;
# see docs/context/plan-issue-258-resumable-apply.md for the full scope)
# ---------------------------------------------------------------------------


def try_apply_dry_run_reverse(patch_path: Path, repo_path: Path) -> ApplyCheckStatus:
    """Run ``git apply --check --reverse`` against *patch_path*.

    Returns the same tri-state as :func:`try_apply_dry_run`.  PASSED means
    the reverse of the patch applies cleanly, which is evidence that the
    forward patch is already present in the working tree.
    """
    try:
        res = subprocess.run(
            ["git", "apply", "--check", "--reverse", str(patch_path)],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=30,
        )
        if res.returncode == 0:
            return ApplyCheckStatus.PASSED
        return ApplyCheckStatus.CONFLICT
    except FileNotFoundError:
        return ApplyCheckStatus.ERROR
    except Exception:
        return ApplyCheckStatus.ERROR


def head_tree_sha(repo_path: Path) -> str | None:
    """Return the tree SHA of HEAD (``HEAD^{tree}``), or None on failure."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode == 0:
            return res.stdout.strip()
        return None
    except Exception:
        return None


def _list_untracked(repo_path: Path) -> set[str] | None:
    """Return the set of untracked non-ignored files, or None on error."""
    try:
        res = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "ls-files",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0:
            return None
        return {line for line in res.stdout.splitlines() if line}
    except Exception:
        return None


def working_tree_equals_expected_state(
    patch_path: Path,
    repo_path: Path,
    expected_baseline_tree_sha: str,
) -> bool:
    """Check that the working tree equals baseline + patch with no residue.

    Uses a temporary Git index in a scratch directory (never copies the
    working tree).  Algorithm:

    1. ``read-tree <baseline>`` into temp index.
    2. ``apply --cached patch.diff`` (forward) into temp index.
    3. Check untracked files: the working tree must have none beyond what
       the patch itself adds.
    4. ``git -c core.filemode=false diff-files --quiet`` against temp index.

    Returns False on any error (never raises).

    Only handles the clean-tree case (the initial run was on a clean
    working tree). Preserving pre-existing dirt across a resume is future
    scope — see docs/context/plan-issue-258-resumable-apply.md (Part 3).
    """
    try:
        return _working_tree_check(patch_path, repo_path, expected_baseline_tree_sha)
    except Exception:
        return False


def _working_tree_check(
    patch_path: Path,
    repo_path: Path,
    expected_baseline_tree_sha: str,
) -> bool:
    with tempfile.TemporaryDirectory(prefix="pf_resume_") as scratch:
        index_path = str(Path(scratch) / "index")
        env = {**os.environ, "GIT_INDEX_FILE": index_path}

        # Step 1: read-tree baseline into temp index
        res = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "read-tree",
                expected_baseline_tree_sha,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if res.returncode != 0:
            return False

        # Step 2: apply patch FORWARD into temp index (--cached)
        res = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "apply",
                "--cached",
                str(patch_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if res.returncode != 0:
            return False

        # `read-tree`/`apply --cached` populate index entries with zeroed
        # stat info (size, mtime, ...) since they never touch the working
        # tree. `diff-files` uses cached size as a fast-reject before
        # hashing — a cached size of 0 against a real, non-empty file is
        # treated as conclusive proof of a difference, so Step 4 below would
        # report every legitimately-matching file as residue (verified
        # empirically). Refreshing the temp index against the real working
        # tree files fixes the cached stat so the later hash comparison
        # actually runs.
        subprocess.run(
            ["git", "-C", str(repo_path), "update-index", "--refresh"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        # `apply_patch` (used by the actual apply command) runs plain
        # `git apply`, which never touches the real index — so any new file
        # the patch added shows up as untracked in the real working tree
        # even when nothing is actually wrong. Files the temp index (built
        # from baseline + patch above) already tracks are expected, not
        # residue, and must be excluded from the untracked comparison below.
        patch_tracked_res = subprocess.run(
            ["git", "-C", str(repo_path), "ls-files"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if patch_tracked_res.returncode != 0:
            return False
        patch_tracked_paths = {line for line in patch_tracked_res.stdout.splitlines() if line}

        # Step 3: untracked file comparison -- clean case must have none
        # beyond what the patch itself introduced.
        current_untracked = _list_untracked(repo_path)
        if current_untracked is None:
            return False
        current_untracked -= patch_tracked_paths
        if current_untracked:
            return False

        # Step 4: diff-files --quiet (content only, ignore mode)
        res = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "-c",
                "core.filemode=false",
                "diff-files",
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        return res.returncode == 0


def _run_git_safe(args: list[str], *, timeout: int = 30, **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` wrapper for the dirt-capture helpers below.

    Converts ``FileNotFoundError`` (git not installed) and
    ``TimeoutExpired`` into a synthetic non-zero-returncode
    ``CompletedProcess`` instead of letting them propagate as raw
    exceptions, so every ``if res.returncode != 0`` check in this section
    is a complete failure story, not just a check on git's own exits.
    """
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(args, 127, "", f"git executable not found: {e}")
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(args, 124, "", f"git command timed out: {e}")


def _has_dirty_submodules(repo_path: Path) -> bool:
    """Return True if any submodule has uncommitted/uninitialized state.

    Uses ``git submodule status``, whose porcelain output prefixes each
    line with a status character (``+`` = checked-out commit differs from
    the index, ``-`` = not initialized, ``U`` = merge conflicts). ``git
    diff --submodule=short --quiet`` was considered but rejected: ``--quiet``
    suppresses all stdout, which would make any check based on its output
    a permanent no-op.

    Raises ``ValueError`` if the check itself fails (git missing, timeout,
    or a non-zero exit for a reason other than "no submodules") -- a
    failed lookup must not be silently treated as "no dirty submodules",
    since that would let stash_create_dirt proceed and later have
    force_reset_apply's ``git clean -fd`` destroy uncaptured submodule
    changes.
    """
    res = _run_git_safe(["git", "-C", str(repo_path), "submodule", "status"])
    if res.returncode != 0:
        raise ValueError(f"failed to check submodule status: {res.stderr}")
    return any(line[:1] in ("+", "-", "U") for line in res.stdout.splitlines() if line)


def stash_create_dirt(repo_path: Path) -> str | None:
    """Capture tracked and untracked working-tree dirt as a stash commit.

    Does not create a ``refs/stash`` entry and does not mutate the working
    tree or the real index. Returns the resulting commit SHA, or ``None``
    if the tree has no dirt to capture.

    Raises ``ValueError`` if HEAD does not exist, if any submodule has
    uncommitted state, or if untracked files cannot be enumerated -- all
    fail-closed conditions where capturing partial/incorrect dirt would be
    worse than aborting before any mutation.
    """
    head_res = _run_git_safe(["git", "-C", str(repo_path), "rev-parse", "HEAD"])
    if head_res.returncode != 0:
        raise ValueError("repository has no HEAD commit; cannot capture dirt")
    head_sha = head_res.stdout.strip()

    if _has_dirty_submodules(repo_path):
        raise ValueError(
            "dirty submodules detected; commit or stash submodule changes "
            "before running --allow-dirty"
        )

    tracked_res = _run_git_safe(["git", "-C", str(repo_path), "stash", "create"])
    if tracked_res.returncode != 0:
        raise ValueError(f"failed to capture tracked dirt: {tracked_res.stderr}")
    tracked_sha = tracked_res.stdout.strip() or None

    untracked = _list_untracked(repo_path)
    if untracked is None:
        raise ValueError("failed to enumerate untracked files")

    if tracked_sha is None and not untracked:
        return None

    if not untracked:
        # stash create's own commit already has the canonical 2-parent
        # (HEAD, index) shape that `stash apply` expects.
        return tracked_sha

    with tempfile.TemporaryDirectory(prefix="pf_dirt_") as scratch:
        index_path = str(Path(scratch) / "index")
        env = {**os.environ, "GIT_INDEX_FILE": index_path}

        stdin_paths = "\n".join(sorted(untracked))
        update_res = _run_git_safe(
            ["git", "-C", str(repo_path), "update-index", "--add", "--stdin"],
            input=stdin_paths,
            timeout=60,
            env=env,
        )
        if update_res.returncode != 0:
            raise ValueError(f"failed to stage untracked files: {update_res.stderr}")

        write_tree_res = _run_git_safe(["git", "-C", str(repo_path), "write-tree"], env=env)
        if write_tree_res.returncode != 0:
            raise ValueError(f"failed to write untracked tree: {write_tree_res.stderr}")
        untracked_tree = write_tree_res.stdout.strip()

        commit_res = _run_git_safe(
            [
                "git",
                "-C",
                str(repo_path),
                "commit-tree",
                untracked_tree,
                "-m",
                "untracked files on stash",
            ]
        )
        if commit_res.returncode != 0:
            raise ValueError(f"failed to commit untracked tree: {commit_res.stderr}")
        untracked_commit = commit_res.stdout.strip()

    if tracked_sha is not None:
        # `stash apply --index` diffs parent2 against parent2^ to restore
        # the staged/unstaged split. parent2 must be the raw index-state
        # commit (tracked_sha's own 2nd parent), not tracked_sha itself --
        # reusing tracked_sha directly here would give parent2 the same
        # tree as the top commit, matching the canonical `git stash push
        # -u` structure exactly rather than relying on it happening to
        # work by coincidence.
        index_parent_res = _run_git_safe(
            ["git", "-C", str(repo_path), "rev-parse", f"{tracked_sha}^2"]
        )
        if index_parent_res.returncode != 0:
            raise ValueError(f"failed to resolve tracked index commit: {index_parent_res.stderr}")
        index_parent = index_parent_res.stdout.strip()
        combined_tree_source = tracked_sha
    else:
        # `stash apply` diffs parent2 against parent2^ to compute the
        # tracked-changes portion, so parent2 must itself have a parent to
        # dereference -- reusing HEAD directly breaks this when HEAD is a
        # root commit. Create a synthetic no-op "index" commit (HEAD's own
        # tree, HEAD as its parent) so parent2^ always resolves, correctly
        # representing "no tracked changes".
        head_tree_res = _run_git_safe(
            ["git", "-C", str(repo_path), "rev-parse", f"{head_sha}^{{tree}}"]
        )
        if head_tree_res.returncode != 0:
            raise ValueError(f"failed to resolve HEAD tree: {head_tree_res.stderr}")
        head_tree = head_tree_res.stdout.strip()
        empty_index_res = _run_git_safe(
            [
                "git",
                "-C",
                str(repo_path),
                "commit-tree",
                head_tree,
                "-p",
                head_sha,
                "-m",
                "no tracked changes",
            ]
        )
        if empty_index_res.returncode != 0:
            raise ValueError(f"failed to create empty index commit: {empty_index_res.stderr}")
        index_parent = empty_index_res.stdout.strip()
        combined_tree_source = head_sha

    tree_res = _run_git_safe(
        ["git", "-C", str(repo_path), "rev-parse", f"{combined_tree_source}^{{tree}}"]
    )
    if tree_res.returncode != 0:
        raise ValueError(f"failed to resolve dirt tree: {tree_res.stderr}")
    combined_tree = tree_res.stdout.strip()

    final_res = _run_git_safe(
        [
            "git",
            "-C",
            str(repo_path),
            "commit-tree",
            combined_tree,
            "-p",
            head_sha,
            "-p",
            index_parent,
            "-p",
            untracked_commit,
            "-m",
            "patchforge dirt capture",
        ]
    )
    if final_res.returncode != 0:
        raise ValueError(f"failed to combine dirt capture: {final_res.stderr}")
    return final_res.stdout.strip()


def stash_apply_dirt(repo_path: Path, stash_sha: str) -> bool:
    """Restore a captured dirt commit onto the working tree and index."""
    res = _run_git_safe(["git", "-C", str(repo_path), "stash", "apply", "--index", stash_sha])
    return res.returncode == 0


_DIRT_REF_PREFIX = "refs/patchforge/dirt/"


def dirt_ref_name(run_id: str) -> str:
    """Compute the private ref used to anchor a run's captured dirt commit."""
    return f"{_DIRT_REF_PREFIX}{run_id}"


def store_dirt_ref(repo_path: Path, run_id: str, stash_sha: str) -> bool:
    """Anchor a captured dirt commit under a private per-run ref.

    Uses ``git update-ref`` directly on ``refs/patchforge/dirt/{run_id}``
    instead of ``git stash store`` (Part 3's original mechanism): stash-list
    addressing (``stash@{N}``) is positional and shared with the user's own
    ``git stash`` workflow, which is a TOCTOU hazard once there's an
    unbounded time gap between the entry being pushed and later being
    dropped (the resume case) -- concurrent stash activity in that gap can
    shift what ``stash@{0}`` refers to. A per-run ref is addressed by exact
    name, never by position, so no such gap exists, and it never touches
    ``refs/stash`` at all.

    Passing an empty-string old value makes this a create-only, atomic
    operation: it fails (returns ``False``) if the ref already exists,
    rather than silently overwriting a stale ref left by an incomplete
    prior cleanup. An empty string -- not a hard-coded all-zero OID -- is
    used deliberately: git documents it as the null-OID sentinel for this
    exact "must not already exist" case, and unlike a fixed-length zero
    string it is correct regardless of the repository's hash algorithm
    (SHA-1 is 40 hex characters, SHA-256 is 64).
    """
    ref = dirt_ref_name(run_id)
    res = _run_git_safe(["git", "-C", str(repo_path), "update-ref", ref, stash_sha, ""])
    return res.returncode == 0


def delete_dirt_ref(repo_path: Path, run_id: str, expected_sha: str) -> bool:
    """Delete a run's dirt-capture ref, conditional on its current value.

    Passing the expected old value makes the delete atomic and conditional:
    if the ref was already deleted or points somewhere unexpected, this
    fails rather than silently deleting an unrelated/newer value. A failure
    here is not fatal to the caller -- the working tree already has the
    dirt restored by this point; it only means the ref is left behind for
    the orphan advisory (``check_orphaned_dirt_refs``) to surface later.
    """
    ref = dirt_ref_name(run_id)
    res = _run_git_safe(["git", "-C", str(repo_path), "update-ref", "-d", ref, expected_sha])
    return res.returncode == 0


def check_orphaned_dirt_refs(repo_path: Path) -> list[tuple[str, str]]:
    """Return ``(run_id, sha)`` for every dirt-capture ref currently present.

    ``refs/patchforge/dirt/`` is PatchForge's own private namespace -- unlike
    Part 3's ``refs/stash``-based lookup, nothing else is expected to create
    refs there, so every entry found is unambiguously ours and the run_id is
    read directly from the ref name (no reflog-message grepping or
    run.json-wide scan needed to identify which run it belongs to).
    """
    res = _run_git_safe(
        [
            "git",
            "-C",
            str(repo_path),
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            _DIRT_REF_PREFIX,
        ]
    )
    if res.returncode != 0 or not res.stdout.strip():
        return []
    orphans: list[tuple[str, str]] = []
    for line in res.stdout.strip().splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        refname, sha = parts
        run_id = refname[len(_DIRT_REF_PREFIX) :]
        if run_id and sha:
            orphans.append((run_id, sha))
    return orphans


def has_merge_conflicts(repo_path: Path) -> bool:
    """Return True if the working tree currently has unmerged paths.

    Used to distinguish a clean no-op failure from a partial merge that left
    conflict markers behind when ``stash_apply_dirt`` returns ``False`` --
    ``git stash apply --index`` has real 3-way-merge semantics, not a simple
    all-or-nothing apply, so its failure alone doesn't say which happened.
    Returns False (fail-open to "no conflicts reported") if the status check
    itself fails, since this is advisory messaging only, not a safety gate.
    """
    res = _run_git_safe(["git", "-C", str(repo_path), "status", "--porcelain=v1"])
    if res.returncode != 0:
        return False
    conflict_codes = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
    return any(line[:2] in conflict_codes for line in res.stdout.splitlines())
