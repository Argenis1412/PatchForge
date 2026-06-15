from __future__ import annotations

import re
import subprocess
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
