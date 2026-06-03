import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from orchestrator.doctor import (
    check,
    check_api_keys,
    check_command_available,
    check_git,
    check_pyproject,
    check_pytest,
    check_ruff,
    check_workspace,
    detect_test_suite,
)
from orchestrator.main import app
from orchestrator.schemas.doctor import CheckStatus, DoctorResult

runner = CliRunner()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    readme = path / "README.md"
    readme.write_text("Hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"], cwd=path, check=True, capture_output=True
    )


def _make_pyproject(path: Path, content: str | None = None) -> None:
    if content is None:
        content = '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    (path / "pyproject.toml").write_text(content, encoding="utf-8")


def _make_orchestrator_json(path: Path, overrides: dict) -> None:
    (path / "orchestrator.json").write_text(json.dumps(overrides), encoding="utf-8")


# ---------------------------------------------------------------------------
# detect_test_suite
# ---------------------------------------------------------------------------


class TestDetectTestSuite:
    def test_tests_directory(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        assert detect_test_suite(tmp_path)

    def test_test_directory(self, tmp_path: Path):
        (tmp_path / "test").mkdir()
        assert detect_test_suite(tmp_path)

    def test_pytest_ini(self, tmp_path: Path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        assert detect_test_suite(tmp_path)

    def test_conftest_py(self, tmp_path: Path):
        (tmp_path / "conftest.py").write_text("# conftest\n")
        assert detect_test_suite(tmp_path)

    def test_tool_pytest_ini_options_in_pyproject(self, tmp_path: Path):
        _make_pyproject(
            tmp_path,
            '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
        )
        pyproject = {"tool": {"pytest": {"ini_options": {"testpaths": ["tests"]}}}}
        assert detect_test_suite(tmp_path, pyproject)

    def test_test_glob(self, tmp_path: Path):
        (tmp_path / "test_foo.py").write_text("def test_foo(): pass\n")
        assert detect_test_suite(tmp_path)

    def test_glob_alternative(self, tmp_path: Path):
        (tmp_path / "foo_test.py").write_text("def test_foo(): pass\n")
        assert detect_test_suite(tmp_path)

    def test_no_suite(self, tmp_path: Path):
        assert not detect_test_suite(tmp_path)


# ---------------------------------------------------------------------------
# check_command_available
# ---------------------------------------------------------------------------


class TestCheckCommandAvailable:
    def test_known_command(self):
        found, version = check_command_available("python")
        assert found
        assert "Python" in version

    def test_unknown_command(self):
        found, version = check_command_available("this-command-does-not-exist")
        assert not found
        assert version == ""


# ---------------------------------------------------------------------------
# check_git
# ---------------------------------------------------------------------------


class TestCheckGit:
    def test_pass(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        result, branch, head, dirty = check_git(repo)
        assert result.status == CheckStatus.PASS
        assert branch in ("main", "master")
        assert len(head) == 40
        assert dirty is False

    def test_fail_not_a_repo(self, tmp_path: Path):
        result, branch, head, dirty = check_git(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert branch is None
        assert head is None
        assert dirty is None


# ---------------------------------------------------------------------------
# check_workspace
# ---------------------------------------------------------------------------


class TestCheckWorkspace:
    def test_pass(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        result, ws_path = check_workspace(repo)
        assert result.status == CheckStatus.PASS
        assert ws_path is not None
        assert Path(ws_path).is_absolute()

    def test_fail_when_inside_target(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        _make_orchestrator_json(repo, {"workspace_path": str(repo / "workspace")})
        result, ws_path = check_workspace(repo)
        assert result.status == CheckStatus.FAIL
        assert ws_path is None


# ---------------------------------------------------------------------------
# check_pyproject
# ---------------------------------------------------------------------------


class TestCheckPyproject:
    def test_pass(self, tmp_path: Path):
        _make_pyproject(tmp_path)
        result, data = check_pyproject(tmp_path)
        assert result.status == CheckStatus.PASS
        assert data is not None
        assert data["build-system"]["build-backend"] == "hatchling.build"

    def test_fail_no_file(self, tmp_path: Path):
        result, data = check_pyproject(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert data is None

    def test_fail_invalid_toml(self, tmp_path: Path):
        _make_pyproject(tmp_path, content="@@@ invalid toml [[[\n")
        result, data = check_pyproject(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert data is None

    def test_fail_missing_build_system(self, tmp_path: Path):
        _make_pyproject(tmp_path, content="[project]\nname = 'foo'\n")
        result, data = check_pyproject(tmp_path)
        assert result.status == CheckStatus.FAIL
        assert data is not None  # parsed but missing build-system


# ---------------------------------------------------------------------------
# check_ruff
# ---------------------------------------------------------------------------


class TestCheckRuff:
    def test_pass_when_ruff_available(self, monkeypatch):
        def fake_check(cmd):
            return (True, "ruff 0.9.0")

        monkeypatch.setattr("orchestrator.doctor.check_command_available", fake_check)
        result = check_ruff(Path("/nonexistent"))
        assert result.status == CheckStatus.PASS

    def test_fail_when_ruff_unavailable(self, monkeypatch):
        def fake_check(cmd):
            return (False, "")

        monkeypatch.setattr("orchestrator.doctor.check_command_available", fake_check)
        result = check_ruff(Path("/nonexistent"))
        assert result.status == CheckStatus.FAIL

    def test_pass_with_lint_command_config(self, tmp_path: Path):
        _make_orchestrator_json(tmp_path, {"lint_command": ["ruff", "check", "."]})

        def fake_check(cmd):
            return (False, "")

        from unittest.mock import patch

        with patch("orchestrator.doctor.check_command_available", fake_check):
            result = check_ruff(tmp_path)
        assert result.status == CheckStatus.PASS
        assert "explicitly configured" in result.message


# ---------------------------------------------------------------------------
# check_pytest
# ---------------------------------------------------------------------------


class TestCheckPytest:
    def _mock_pytest_ok(self, monkeypatch, cmd):
        monkeypatch.setattr(
            "orchestrator.doctor.check_command_available",
            lambda cmd: (True, "pytest 8.3.0"),
        )

    def test_pass_with_test_dir(self, tmp_path: Path, monkeypatch):
        (tmp_path / "tests").mkdir()
        self._mock_pytest_ok(monkeypatch, cmd="pytest")
        result = check_pytest(tmp_path)
        assert result.status == CheckStatus.PASS

    def test_fail_no_pytest_no_config(self, monkeypatch):
        monkeypatch.setattr(
            "orchestrator.doctor.check_command_available",
            lambda cmd: (False, ""),
        )
        result = check_pytest(Path("/nonexistent"))
        assert result.status == CheckStatus.FAIL

    def test_fail_pytest_available_but_no_tests(self, tmp_path: Path, monkeypatch):
        self._mock_pytest_ok(monkeypatch, cmd="pytest")
        result = check_pytest(tmp_path)
        assert result.status == CheckStatus.FAIL

    def test_pass_with_test_command_config(self, tmp_path: Path, monkeypatch):
        (tmp_path / "tests").mkdir()
        _make_orchestrator_json(tmp_path, {"test_command": ["pytest", "."]})
        monkeypatch.setattr("orchestrator.doctor.check_command_available", lambda cmd: (False, ""))
        result = check_pytest(tmp_path)
        assert result.status == CheckStatus.PASS
        assert "explicitly configured" in result.message

    def test_fail_test_command_config_but_no_suite(self, tmp_path: Path, monkeypatch):
        _make_orchestrator_json(tmp_path, {"test_command": ["pytest", "."]})
        monkeypatch.setattr("orchestrator.doctor.check_command_available", lambda cmd: (False, ""))
        result = check_pytest(tmp_path)
        assert result.status == CheckStatus.FAIL


# ---------------------------------------------------------------------------
# check_api_keys
# ---------------------------------------------------------------------------


class TestCheckApiKeys:
    def test_all_missing(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        results = check_api_keys()
        assert len(results) == 3
        for r in results:
            assert r.status == CheckStatus.WARN
            assert r.required is False
        names = {r.name for r in results}
        assert names == {"anthropic_api_key", "google_api_key", "groq_api_key"}
        assert all("not configured" in r.message for r in results)
        assert all("Set" in r.fix_hint for r in results)

    def test_all_present(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
        monkeypatch.setenv("GOOGLE_API_KEY", "AIza-xxx")
        monkeypatch.setenv("GROQ_API_KEY", "gsk-xxx")
        results = check_api_keys()
        assert len(results) == 0

    def test_partial_presence(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GROQ_API_KEY", "gsk-xxx")
        results = check_api_keys()
        assert len(results) == 1
        assert results[0].name == "google_api_key"
        assert results[0].status == CheckStatus.WARN
        assert results[0].required is False

    def test_v1_supported_unaffected(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _make_pyproject(repo)
        (repo / "tests").mkdir()
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add pyproject and tests"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        def fake_check(cmd):
            return (True, "ok")

        from unittest.mock import patch
        with patch("orchestrator.doctor.check_command_available", fake_check):
            result = check(repo)

        assert result.v1_supported is True
        api_names = {"anthropic_api_key", "google_api_key", "groq_api_key"}
        api_warns = [c for c in result.checks if c.name in api_names]
        assert len(api_warns) == 3
        for w in api_warns:
            assert w.status == CheckStatus.WARN
            assert w.required is False


# ---------------------------------------------------------------------------
# check (aggregator)
# ---------------------------------------------------------------------------


class TestCheck:
    def test_v1_supported_true_when_all_pass(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _make_pyproject(repo)
        (repo / "tests").mkdir()
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add pyproject and tests"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        def fake_check(cmd):
            return (True, "ok")

        from unittest.mock import patch

        with patch("orchestrator.doctor.check_command_available", fake_check):
            result = check(repo)

        assert result.v1_supported is True
        assert result.git_branch in ("main", "master")
        assert result.is_dirty is False

    def test_v1_supported_false_when_any_required_check_fails(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _make_pyproject(repo)

        # pytest available but no test suite → FAIL
        def fake_check(cmd):
            return (True, "ok")

        from unittest.mock import patch

        with patch("orchestrator.doctor.check_command_available", fake_check):
            result = check(repo)

        assert result.v1_supported is False
        pytest_check = next(c for c in result.checks if c.name == "pytest")
        assert pytest_check.status == CheckStatus.FAIL

    def test_dirty_tree_reported(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _make_pyproject(repo)
        (repo / "tests").mkdir()

        (repo / "README.md").write_text("modified\n")

        def fake_check(cmd):
            return (True, "ok")

        from unittest.mock import patch

        with patch("orchestrator.doctor.check_command_available", fake_check):
            result = check(repo)

        assert result.v1_supported is True
        assert result.is_dirty is True
        warn_checks = [c for c in result.checks if c.name == "working_tree"]
        assert len(warn_checks) == 1
        assert warn_checks[0].status == CheckStatus.WARN

    def test_does_not_write_to_target(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _make_pyproject(repo)
        (repo / "tests").mkdir()

        before = set(repo.iterdir())

        def fake_check(cmd):
            return (True, "ok")

        from unittest.mock import patch

        with patch("orchestrator.doctor.check_command_available", fake_check):
            check(repo)

        after = set(repo.iterdir())
        assert before == after


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestDoctorCLI:
    def test_json_output(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _make_pyproject(repo)
        (repo / "tests").mkdir()

        def fake_check(cmd):
            return (True, "ok")

        from unittest.mock import patch

        with patch("orchestrator.doctor.check_command_available", fake_check):
            result = runner.invoke(app, ["doctor", str(repo), "--json"])

        assert result.exit_code == 0
        parsed = DoctorResult.model_validate_json(result.stdout)
        assert parsed.v1_supported is True
        assert len(parsed.checks) > 0

    def test_exit_code_0_when_supported(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _make_pyproject(repo)
        (repo / "tests").mkdir()

        def fake_check(cmd):
            return (True, "ok")

        from unittest.mock import patch

        with patch("orchestrator.doctor.check_command_available", fake_check):
            result = runner.invoke(app, ["doctor", str(repo)])

        assert result.exit_code == 0

    def test_exit_code_1_when_unsupported(self, tmp_path: Path):
        result = runner.invoke(app, ["doctor", str(tmp_path)])

        assert result.exit_code == 1

    def test_does_not_require_api_keys(self, tmp_path: Path):
        result = runner.invoke(app, ["doctor", str(tmp_path), "--json"])
        assert result.exit_code == 1
        parsed = DoctorResult.model_validate_json(result.stdout)
        # doctor should never crash, only produce structured results
        assert parsed.checks[0].status == CheckStatus.FAIL  # no git
