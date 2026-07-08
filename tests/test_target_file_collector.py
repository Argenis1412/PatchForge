from pathlib import Path

import pytest

from orchestrator.agents.architect.file_collector import (
    build_target_files_block,
    collect_target_files,
)
from orchestrator.schemas.config import TargetConfig


def _make_config(target_path: Path) -> TargetConfig:
    workspace = target_path.parent / "_workspace"
    workspace.mkdir(exist_ok=True)
    return TargetConfig(target_path=target_path, workspace_path=workspace)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


class TestCollectTargetFiles:
    @pytest.mark.unit
    def test_none_config(self):
        paths, truncated, total = collect_target_files(None)
        assert paths == []
        assert truncated is False
        assert total == 0

    @pytest.mark.unit
    def test_basic_listing(self, tmp_path):
        _touch(tmp_path / "src" / "main.py")
        _touch(tmp_path / "src" / "utils.py")
        _touch(tmp_path / "README.md")
        config = _make_config(tmp_path)

        paths, truncated, total = collect_target_files(config)

        assert truncated is False
        assert total == 3
        assert paths == ["README.md", "src/main.py", "src/utils.py"]

    @pytest.mark.unit
    def test_posix_paths(self, tmp_path):
        _touch(tmp_path / "a" / "b" / "c.py")
        config = _make_config(tmp_path)

        paths, _, _ = collect_target_files(config)

        assert paths == ["a/b/c.py"]
        assert "\\" not in paths[0]

    @pytest.mark.unit
    def test_ignore_dirs_honored(self, tmp_path):
        _touch(tmp_path / "src" / "app.py")
        _touch(tmp_path / "__pycache__" / "app.cpython-312.pyc")
        _touch(tmp_path / ".git" / "config")
        config = _make_config(tmp_path)

        paths, _, _ = collect_target_files(config)

        assert "src/app.py" in paths
        assert not any("__pycache__" in p for p in paths)
        assert not any(".git" in p for p in paths)

    @pytest.mark.unit
    def test_extra_ignore_dirs_excluded(self, tmp_path):
        _touch(tmp_path / "src" / "app.py")
        _touch(tmp_path / "dist" / "bundle.js")
        _touch(tmp_path / "build" / "output.js")
        _touch(tmp_path / "htmlcov" / "index.html")
        config = _make_config(tmp_path)

        paths, _, _ = collect_target_files(config)

        assert "src/app.py" in paths
        assert not any(p.startswith("dist/") for p in paths)
        assert not any(p.startswith("build/") for p in paths)
        assert not any(p.startswith("htmlcov/") for p in paths)

    @pytest.mark.unit
    def test_all_extensions_included(self, tmp_path):
        _touch(tmp_path / "Dockerfile")
        _touch(tmp_path / "pyproject.toml")
        _touch(tmp_path / ".github" / "workflows" / "ci.yml")
        _touch(tmp_path / "src" / "main.py")
        config = _make_config(tmp_path)

        paths, _, _ = collect_target_files(config)

        assert "Dockerfile" in paths
        assert "pyproject.toml" in paths
        assert ".github/workflows/ci.yml" in paths
        assert "src/main.py" in paths

    @pytest.mark.unit
    def test_truncation(self, tmp_path):
        for i in range(5):
            _touch(tmp_path / f"file_{i:02d}.py")
        config = _make_config(tmp_path)

        paths, truncated, total = collect_target_files(config, max_paths=3)

        assert truncated is True
        assert total == 5
        assert len(paths) == 3
        assert paths == ["file_00.py", "file_01.py", "file_02.py"]

    @pytest.mark.unit
    def test_empty_target(self, tmp_path):
        config = _make_config(tmp_path)

        paths, truncated, total = collect_target_files(config)

        assert paths == []
        assert truncated is False
        assert total == 0


class TestBuildTargetFilesBlock:
    @pytest.mark.unit
    def test_none_config(self):
        block, paths, truncated, total = build_target_files_block(None)
        assert "[TARGET FILES]" in block
        assert "(unavailable — no target config provided)" in block
        assert paths == []
        assert truncated is False
        assert total == 0

    @pytest.mark.unit
    def test_empty_listing(self, tmp_path):
        config = _make_config(tmp_path)
        block, paths, truncated, total = build_target_files_block(config)
        assert "[TARGET FILES]" in block
        assert "(no files found in target directory)" in block
        assert paths == []

    @pytest.mark.unit
    def test_normal_listing(self, tmp_path):
        _touch(tmp_path / "src" / "main.py")
        _touch(tmp_path / "README.md")
        config = _make_config(tmp_path)

        block, paths, truncated, total = build_target_files_block(config)

        assert "[TARGET FILES]" in block
        assert "README.md" in block
        assert "src/main.py" in block
        assert "truncated" not in block
        assert truncated is False
        assert total == 2

    @pytest.mark.unit
    def test_truncated_block_has_top_level_dirs(self, tmp_path, monkeypatch):
        for i in range(5):
            _touch(tmp_path / "src" / f"mod_{i:02d}.py")
        _touch(tmp_path / "docs" / "guide.md")
        _touch(tmp_path / "tests" / "test_a.py")
        config = _make_config(tmp_path)

        import orchestrator.agents.architect.file_collector as fc

        orig = fc.collect_target_files
        monkeypatch.setattr(
            fc,
            "collect_target_files",
            lambda cfg, max_paths=500: orig(cfg, max_paths=3),
        )

        block, paths, truncated, total = build_target_files_block(config)

        assert "(truncated: showing 3 of 7 paths, alphabetical order)" in block
        assert "(top-level dirs present:" in block
        assert "docs/" in block
        assert truncated is True
        assert total == 7
        assert len(paths) == 3
