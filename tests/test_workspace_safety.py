import os
import subprocess
from pathlib import Path

import pytest

from orchestrator.schemas.config import TargetConfig, default_workspace_path


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)


def _git_status_short(path: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_default_workspace_is_external_and_stable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    before = _git_status_short(repo)
    config = TargetConfig.load(target_path=repo)

    assert config.workspace_path == default_workspace_path(repo)
    assert not config.workspace_path.is_relative_to(repo.resolve())
    assert not (repo / "workspace").exists()
    assert _git_status_short(repo) == before == ""


def test_explicit_external_workspace_is_allowed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
    config = TargetConfig.load(target_path=repo, workspace_path=workspace)

    assert config.workspace_path == workspace.resolve()
    assert not config.workspace_path.is_relative_to(repo.resolve())


def test_workspace_inside_target_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    with pytest.raises(ValueError, match="outside the target repository"):
        TargetConfig.load(target_path=repo, workspace_path=repo / "workspace")


def test_direct_targetconfig_construction_rejects_internal_workspace(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    with pytest.raises(ValueError, match="outside the target repository"):
        TargetConfig(target_path=repo, workspace_path=repo / "workspace")


def test_symlink_workspace_resolving_inside_target_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    internal = repo / "internal"
    internal.mkdir()

    links_dir = tmp_path.parent / f"{tmp_path.name}-links"
    links_dir.mkdir(exist_ok=True)
    link_path = links_dir / "workspace-link"

    try:
        os.symlink(internal, link_path, target_is_directory=True)
    except (AttributeError, NotImplementedError, OSError):
        pytest.skip("symlinks are not available in this environment")

    with pytest.raises(ValueError, match="outside the target repository"):
        TargetConfig.load(target_path=repo, workspace_path=link_path)
