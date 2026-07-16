"""Approval Provenance domain logic (P4-5).

Level-2 module: decides *what a captured value means* (source selection
between CI and local, mapping to ``triggered_by``/``approved_by``, fallback
rules). Callers pass in raw values already read by Level-1 wrappers
(``git.py``) or by the CLI entry point (``GITHUB_ACTOR``) — this module has
no environment or subprocess access of its own, keeping it independent of
``git.py``/``pipeline.py``/``main.py`` per the project's invariants.

Record only, not policy: these values describe who ran a stage, not who is
authorized to. Authorization stays out of scope.
"""

from __future__ import annotations

__all__ = ["resolve_approved_by", "resolve_triggered_by"]

from pathlib import Path

from orchestrator.git import git_config_user_email, git_config_user_name


def _local_identity(repo_root: Path) -> str | None:
    """Return a ``local:{name} <{email}>`` identity string, or None.

    Degrades gracefully when only one of name/email is configured, never
    emitting a literal "None" into the identity string.
    """
    name = git_config_user_name(repo_root)
    email = git_config_user_email(repo_root)

    if name and email:
        return f"local:{name} <{email}>"
    if name:
        return f"local:{name}"
    if email:
        return f"local:<{email}>"
    return None


def resolve_triggered_by(
    *,
    repo_root: Path | None = None,
    github_actor: str | None = None,
) -> str | None:
    """Return the ``triggered_by`` provenance string for a new run.

    CI identity (``github_actor``) takes precedence when both sources are
    available, since it is the more specific context. Falls back to the
    local git identity, then to None when neither source resolves.
    """
    if github_actor:
        return f"github:{github_actor}"
    if repo_root is not None:
        return _local_identity(repo_root)
    return None


def resolve_approved_by(repo_root: Path) -> str | None:
    """Return the ``approved_by`` provenance string at patch-approval time.

    Only meaningful for the local ``apply`` command — that is the actual
    human gate. CI runs never call this; their approval happens at PR
    merge, outside PatchForge's control.
    """
    return _local_identity(repo_root)
