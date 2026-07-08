from pathlib import Path

import pytest

from orchestrator.agents.architect.file_collector import (
    _detect_packages,
    _summarize_python_file,
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
        paths, truncated, total, pkg_dirs = collect_target_files(None)
        assert paths == []
        assert truncated is False
        assert total == 0
        assert pkg_dirs == set()

    @pytest.mark.unit
    def test_basic_listing(self, tmp_path):
        _touch(tmp_path / "src" / "main.py")
        _touch(tmp_path / "src" / "utils.py")
        _touch(tmp_path / "README.md")
        config = _make_config(tmp_path)

        paths, truncated, total, _ = collect_target_files(config)

        assert truncated is False
        assert total == 3
        assert paths == ["README.md", "src/main.py", "src/utils.py"]

    @pytest.mark.unit
    def test_posix_paths(self, tmp_path):
        _touch(tmp_path / "a" / "b" / "c.py")
        config = _make_config(tmp_path)

        paths, _, _, _ = collect_target_files(config)

        assert paths == ["a/b/c.py"]
        assert "\\" not in paths[0]

    @pytest.mark.unit
    def test_ignore_dirs_honored(self, tmp_path):
        _touch(tmp_path / "src" / "app.py")
        _touch(tmp_path / "__pycache__" / "app.cpython-312.pyc")
        _touch(tmp_path / ".git" / "config")
        config = _make_config(tmp_path)

        paths, _, _, _ = collect_target_files(config)

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

        paths, _, _, _ = collect_target_files(config)

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

        paths, _, _, _ = collect_target_files(config)

        assert "Dockerfile" in paths
        assert "pyproject.toml" in paths
        assert ".github/workflows/ci.yml" in paths
        assert "src/main.py" in paths

    @pytest.mark.unit
    def test_truncation(self, tmp_path):
        for i in range(5):
            _touch(tmp_path / f"file_{i:02d}.py")
        config = _make_config(tmp_path)

        paths, truncated, total, _ = collect_target_files(config, max_paths=3)

        assert truncated is True
        assert total == 5
        assert len(paths) == 3
        assert paths == ["file_00.py", "file_01.py", "file_02.py"]

    @pytest.mark.unit
    def test_empty_target(self, tmp_path):
        config = _make_config(tmp_path)

        paths, truncated, total, _ = collect_target_files(config)

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


# ---------------------------------------------------------------------------
# _summarize_python_file unit tests (D-005)
# ---------------------------------------------------------------------------


class TestSummarizePythonFile:
    @pytest.mark.unit
    def test_docstring_and_defs(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text(
            '"""Module docstring."""\n\ndef foo():\n    pass\n\nclass Bar:\n    pass\n',
            encoding="utf-8",
        )
        result = _summarize_python_file(f)
        assert result is not None
        assert "Module docstring." in result
        assert "foo()" in result
        assert "Bar" in result
        assert "|" in result

    @pytest.mark.unit
    def test_async_def(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("async def handle():\n    pass\n", encoding="utf-8")
        result = _summarize_python_file(f)
        assert result is not None
        assert "handle()" in result

    @pytest.mark.unit
    def test_syntax_error(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("def (broken syntax", encoding="utf-8")
        assert _summarize_python_file(f) is None

    @pytest.mark.unit
    def test_no_symbols(self, tmp_path):
        f = tmp_path / "consts.py"
        f.write_text("X = 1\nY = 2\n", encoding="utf-8")
        assert _summarize_python_file(f) is None

    @pytest.mark.unit
    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("", encoding="utf-8")
        assert _summarize_python_file(f) is None

    @pytest.mark.unit
    def test_io_error(self, tmp_path):
        assert _summarize_python_file(tmp_path / "nonexistent.py") is None

    @pytest.mark.unit
    def test_null_bytes(self, tmp_path):
        f = tmp_path / "null.py"
        f.write_bytes(b"def foo():\n    pass\n\x00")
        assert _summarize_python_file(f) is None

    @pytest.mark.unit
    def test_truncates_docstring(self, tmp_path):
        f = tmp_path / "mod.py"
        long_doc = "A" * 120
        f.write_text(f'"""{long_doc}"""\n\ndef f():\n    pass\n', encoding="utf-8")
        result = _summarize_python_file(f)
        assert result is not None
        assert "…" in result
        doc_part = result.split(" | ")[0]
        assert len(doc_part) <= 80

    @pytest.mark.unit
    def test_caps_names_at_8(self, tmp_path):
        f = tmp_path / "mod.py"
        funcs = "\n".join(f"def func_{i}():\n    pass\n" for i in range(10))
        f.write_text(funcs, encoding="utf-8")
        result = _summarize_python_file(f)
        assert result is not None
        assert "func_7()" in result
        assert "func_8()" not in result


# ---------------------------------------------------------------------------
# _detect_packages unit tests (D-005)
# ---------------------------------------------------------------------------


class TestDetectPackages:
    @pytest.mark.unit
    def test_basic(self):
        paths = ["pkg/__init__.py", "pkg/mod.py", "standalone.py"]
        assert _detect_packages(paths) == {"pkg"}

    @pytest.mark.unit
    def test_nested(self):
        paths = [
            "pkg/__init__.py",
            "pkg/sub/__init__.py",
            "pkg/sub/mod.py",
        ]
        result = _detect_packages(paths)
        assert "pkg" in result
        assert "pkg/sub" in result

    @pytest.mark.unit
    def test_root_init(self):
        paths = ["__init__.py", "core.py"]
        assert "" in _detect_packages(paths)


# ---------------------------------------------------------------------------
# Annotation integration tests (D-005)
# ---------------------------------------------------------------------------


class TestFileCollectorAnnotations:
    @pytest.mark.unit
    def test_package_files_annotated_in_block(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            '"""Package init."""\n\ndef run():\n    pass\n', encoding="utf-8"
        )
        (pkg / "helper.py").write_text("def assist():\n    pass\n", encoding="utf-8")
        config = _make_config(tmp_path)

        block, paths, _, _ = build_target_files_block(config)

        assert "pkg/__init__.py  # Package init. | run()" in block
        assert "pkg/helper.py  # assist()" in block

    @pytest.mark.unit
    def test_non_package_py_not_annotated(self, tmp_path):
        (tmp_path / "standalone.py").write_text(
            '"""Standalone."""\n\ndef main():\n    pass\n', encoding="utf-8"
        )
        config = _make_config(tmp_path)

        block, _, _, _ = build_target_files_block(config)

        assert "standalone.py" in block
        assert "#" not in block

    @pytest.mark.unit
    def test_non_python_in_package_not_annotated(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "config.toml").write_text("[tool]\n", encoding="utf-8")
        config = _make_config(tmp_path)

        block, _, _, _ = build_target_files_block(config)

        assert "config.toml" in block
        for line in block.splitlines():
            if "config.toml" in line:
                assert "#" not in line

    @pytest.mark.unit
    def test_annotation_budget_cap(self, tmp_path, monkeypatch):
        import orchestrator.agents.architect.file_collector as fc

        monkeypatch.setattr(fc, "_ANNOTATION_BUDGET", 50)

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            '"""First module with a reasonably long docstring for testing."""\n'
            "def alpha():\n    pass\n",
            encoding="utf-8",
        )
        (pkg / "second.py").write_text(
            '"""Second module."""\ndef beta():\n    pass\n', encoding="utf-8"
        )
        config = _make_config(tmp_path)

        block, _, _, _ = build_target_files_block(config)

        annotated_lines = [line for line in block.splitlines() if "#" in line]
        assert len(annotated_lines) < 2

    @pytest.mark.unit
    def test_paths_return_not_annotated(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text('"""Pkg."""\ndef run():\n    pass\n', encoding="utf-8")
        config = _make_config(tmp_path)

        _, paths, _, _ = build_target_files_block(config)

        for p in paths:
            assert "#" not in p
