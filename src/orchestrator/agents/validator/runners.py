import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from orchestrator.schemas.validator_output import ToolResult

from .logging import _get_logger

SUBPROCESS_TIMEOUT = 120

IGNORE_DIRS = [
    "node_modules",
    ".venv",
    "__pycache__",
    ".git",
    "workspace",
    ".ruff_cache",
    ".pytest_cache",
]


def _resolve_cmd(cmd_override: list[str] | None, default: list[str]) -> list[str]:
    cmd = list(cmd_override) if cmd_override is not None else list(default)
    if not cmd:
        raise ValueError("Command override must contain at least one token")
    return cmd


def _run(
    cmd: list[str],
    cwd: Path,
    tool_name: str,
    run_id: str,
) -> ToolResult:
    _get_logger().info("[%s] Running %s: %s (cwd=%s)", run_id, tool_name, " ".join(cmd), cwd)
    t0 = time.perf_counter()

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except FileNotFoundError:
        msg = f"Command not found: {cmd[0]} — is it installed and in PATH?"
        _get_logger().error("[%s] %s", run_id, msg)
        return ToolResult(
            tool=tool_name,
            passed=False,
            return_code=-1,
            stderr=msg,
        )
    except subprocess.TimeoutExpired:
        msg = f"Timeout ({SUBPROCESS_TIMEOUT}s) running {cmd[0]}"
        _get_logger().error("[%s] %s", run_id, msg)
        return ToolResult(
            tool=tool_name,
            passed=False,
            return_code=-2,
            stderr=msg,
        )

    elapsed = time.perf_counter() - t0
    passed = proc.returncode in (0, 5)

    _get_logger().info(
        "[%s] %s → %s | rc=%d | latency=%.2fs",
        run_id,
        tool_name,
        "PASS" if passed else "FAIL",
        proc.returncode,
        elapsed,
    )
    if not passed:
        _get_logger().debug("[%s] %s stderr:\n%s", run_id, tool_name, proc.stderr[:2000])

    return ToolResult(
        tool=tool_name,
        passed=passed,
        return_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _collect_staged_files(staging_dir: Path) -> list[Path]:
    if not staging_dir.is_dir():
        return []
    return sorted(p for p in staging_dir.rglob("*") if p.is_file())


def _create_overlay(
    project_root: Path,
    staging_dir: Path,
    ignore_dirs: list[str],
    tmpdir: Path | None = None,
) -> Path:
    if tmpdir is None:
        tmpdir = Path(tempfile.mkdtemp(prefix="val_overlay_"))
    ignore_set = set(ignore_dirs)
    shutil.copytree(
        str(project_root),
        str(tmpdir / project_root.name),
        ignore=lambda src, names: [n for n in names if n in ignore_set],
        dirs_exist_ok=True,
        symlinks=True,
    )
    overlay_root = tmpdir / project_root.name
    for staged_file in _collect_staged_files(staging_dir):
        rel = staged_file.relative_to(staging_dir)
        target = overlay_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_file, target)
    return overlay_root


def _find_frontend_dir(root: Path) -> Path | None:
    for path in root.rglob("package.json"):
        if "node_modules" not in path.parts:
            return path.parent
    return None


def run_ruff(
    run_id: str,
    project_root: Path,
    cmd_override: list[str] | None = None,
    staging_dir: Path | None = None,
) -> ToolResult:
    if staging_dir is not None and staging_dir.is_dir():
        staged_files = _collect_staged_files(staging_dir)
        if staged_files:
            cmd = _resolve_cmd(cmd_override, ["ruff", "check"])
            cmd.extend(str(sf) for sf in staged_files)
            return _run(cmd, project_root, "ruff", run_id)
    cmd = _resolve_cmd(cmd_override, ["ruff", "check", "."])
    return _run(cmd, project_root, "ruff", run_id)


def run_pytest(
    run_id: str,
    project_root: Path,
    cmd_override: list[str] | None = None,
    staging_dir: Path | None = None,
) -> ToolResult:
    if (
        staging_dir is not None
        and staging_dir.is_dir()
        and bool(_collect_staged_files(staging_dir))
    ):
        with tempfile.TemporaryDirectory(prefix="val_overlay_") as tmpdir:
            overlay_root = _create_overlay(project_root, staging_dir, IGNORE_DIRS, Path(tmpdir))
            cmd = _resolve_cmd(cmd_override, ["pytest", ".", "--tb=short", "-q"])
            return _run(cmd, overlay_root, "pytest", run_id)
    cmd = _resolve_cmd(cmd_override, ["pytest", ".", "--tb=short", "-q"])
    return _run(
        cmd,
        project_root,
        "pytest",
        run_id,
    )


def run_tsc(
    run_id: str,
    project_root: Path,
    cmd_override: list[str] | None = None,
    staging_dir: Path | None = None,
) -> ToolResult:
    if (
        staging_dir is not None
        and staging_dir.is_dir()
        and bool(_collect_staged_files(staging_dir))
    ):
        with tempfile.TemporaryDirectory(prefix="val_overlay_") as tmpdir:
            overlay_root = _create_overlay(project_root, staging_dir, IGNORE_DIRS, Path(tmpdir))
            frontend = _find_frontend_dir(overlay_root) or _find_frontend_dir(project_root)
            if frontend is None:
                _get_logger().warning("[%s] frontend/ not found — skip tsc", run_id)
                return ToolResult(
                    tool="tsc",
                    passed=True,
                    return_code=0,
                    stdout="Skipped — frontend/ not found",
                )
            cmd = _resolve_cmd(cmd_override, ["npx", "tsc", "--noEmit"])
            return _run(cmd, frontend, "tsc", run_id)
    frontend = _find_frontend_dir(project_root)
    if frontend is None:
        _get_logger().warning("[%s] frontend/ not found — skip tsc", run_id)
        return ToolResult(
            tool="tsc",
            passed=True,
            return_code=0,
            stdout="Skipped — frontend/ not found",
        )
    cmd = list(cmd_override) if cmd_override is not None else ["npx", "tsc", "--noEmit"]
    return _run(cmd, frontend, "tsc", run_id)
