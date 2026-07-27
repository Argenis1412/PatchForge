"""V2 validator declaration execution and aggregation.

Legacy runners intentionally remain separate: their pass semantics are part of
the V1 contract. This module evaluates raw process results against one V2
declaration at a time.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

from orchestrator.schemas.config import ValidatorConfig
from orchestrator.schemas.validator_output import (
    CoverageStatus,
    ExecutionState,
    OverallStatus,
    ToolResult,
    ValidatorOutput,
)

from .process import ProcessResult, build_isolated_environment, execute_process, prepare_process

_STANDARD_COMMANDS: dict[str, list[str]] = {
    "ruff": [sys.executable, "-I", "-m", "ruff", "check"],
    "pytest": [sys.executable, "-I", "-m", "pytest"],
    "tsc": ["npx", "tsc", "--noEmit"],
    "flake8": ["flake8", "."],
    "mypy": ["mypy", "."],
    "pylint": ["pylint", "."],
    "unittest": [sys.executable, "-I", "-m", "unittest", "discover"],
    "tox": ["tox"],
}

_PYTHON_STANDARD_ADAPTERS = {"ruff", "pytest", "unittest"}


def resolve_validator_command(
    declaration: ValidatorConfig, project_root: Path, scratch_dir: Path | None = None
) -> list[str] | None:
    """Resolve one declaration without executing it.

    Standard Python adapters launch from a private cwd and receive the target
    as an absolute argument, preventing a candidate-root module from becoming
    the adapter implementation.
    """
    command = declaration.command or _STANDARD_COMMANDS.get(declaration.adapter)
    if command is None:
        return None
    command = list(command)
    if declaration.command is not None:
        return command
    if declaration.adapter == "ruff":
        command.extend([str(project_root)])
        if scratch_dir is not None:
            command.extend(["--cache-dir", str(scratch_dir / "ruff-cache")])
    elif declaration.adapter == "pytest":
        command.extend([str(project_root), "--tb=short", "-q"])
        if scratch_dir is not None:
            command.extend(["-o", f"cache_dir={scratch_dir / 'pytest-cache'}"])
    elif declaration.adapter == "unittest":
        command.extend(["-s", str(project_root)])
    elif declaration.adapter == "mypy":
        command[-1] = str(project_root)
        if scratch_dir is not None:
            command.extend(["--cache-dir", str(scratch_dir / "mypy-cache")])
    elif declaration.adapter in {"flake8", "pylint"}:
        command[-1] = str(project_root)
    return command


def _has_frontend(project_root: Path) -> bool:
    return any("node_modules" not in path.parts for path in project_root.rglob("package.json"))


def _raw_result(declaration: ValidatorConfig, project_root: Path, timeout: int) -> ProcessResult:
    if (
        declaration.adapter == "tsc"
        and declaration.command is None
        and not _has_frontend(project_root)
    ):
        return ProcessResult(return_code=None, unavailable=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix="patchforge-validator-", ignore_cleanup_errors=True
        ) as scratch_name:
            scratch = Path(scratch_name)
            command = resolve_validator_command(declaration, project_root, scratch)
            if command is None:
                return ProcessResult(return_code=None, unavailable=True)
            private_cwd = (
                scratch
                if declaration.adapter in _PYTHON_STANDARD_ADAPTERS and declaration.command is None
                else project_root
            )
            return execute_process(
                prepare_process(
                    command, private_cwd, environment=build_isolated_environment(scratch)
                ),
                timeout,
            )
    except ProcessLookupError:
        return ProcessResult(return_code=None, cleanup_failed=True)


def _tree_manifest(project_root: Path) -> dict[str, str]:
    """Return a content manifest including ignored and untracked files."""
    manifest: dict[str, str] = {}
    for path in project_root.rglob("*"):
        if ".git" in path.parts:
            continue
        relative = path.relative_to(project_root).as_posix()
        if path.is_symlink():
            manifest[relative] = f"link:{path.readlink()}"
        elif path.is_file():
            manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            manifest[relative] = "directory"
    return manifest


def _coverage(declaration: ValidatorConfig, state: ExecutionState) -> dict[str, CoverageStatus]:
    roles = [role.value for role in declaration.roles or []]
    if state is not ExecutionState.APPROVED:
        return dict.fromkeys(roles, CoverageStatus.ABSENT)
    declared_only = (
        declaration.adapter in {"command", "tox", "tsc"} or declaration.command is not None
    )
    status = CoverageStatus.DECLARED_ONLY if declared_only else CoverageStatus.VERIFIED
    return dict.fromkeys(roles, status)


def _terminal_state(raw: ProcessResult, declaration: ValidatorConfig) -> ExecutionState:
    if raw.cleanup_failed:
        return ExecutionState.CLEANUP_FAILED
    if raw.timed_out:
        return ExecutionState.TIMEOUT
    if raw.unavailable:
        return ExecutionState.UNAVAILABLE
    if raw.return_code in declaration.success_codes:
        return ExecutionState.APPROVED
    return ExecutionState.FAILED


def _result_for(
    declaration: ValidatorConfig,
    index: int,
    state: ExecutionState,
    raw: ProcessResult | None = None,
) -> ToolResult:
    roles = [role.value for role in declaration.roles or []]
    if raw is None:
        raw = ProcessResult(return_code=None)
    passed = (
        True
        if state is ExecutionState.APPROVED
        else False
        if state
        in {
            ExecutionState.FAILED,
            ExecutionState.TIMEOUT,
        }
        else None
    )
    return ToolResult(
        tool=declaration.adapter,
        adapter=declaration.adapter,
        validator_id=declaration.id,
        declaration_index=index,
        passed=passed,
        return_code=raw.return_code if raw.return_code is not None else -1,
        stdout=raw.stdout,
        stderr=raw.stderr,
        timed_out=state is ExecutionState.TIMEOUT,
        status=state,
        declared_roles=roles,
        role_coverage=_coverage(declaration, state),
    )


def run_v2_validators(
    run_id: str,
    project_root: Path,
    validators: list[ValidatorConfig],
    timeout: int,
) -> ValidatorOutput:
    """Run V2 declarations in order, stopping after any non-approved result."""
    if not validators:
        return ValidatorOutput(
            overall_passed=False,
            overall_status=OverallStatus.INCOMPLETE,
            result_profile="v2",
            tools=[],
            run_id=run_id,
        )

    results: list[ToolResult] = []
    for index, declaration in enumerate(validators):
        before = _tree_manifest(project_root)
        raw = _raw_result(declaration, project_root, timeout)
        state = _terminal_state(raw, declaration)
        if before != _tree_manifest(project_root):
            raw = ProcessResult(
                return_code=raw.return_code,
                stdout=raw.stdout,
                stderr=(
                    raw.stderr + "\nValidation workspace changed during V2 validation."
                ).strip(),
                timed_out=raw.timed_out,
                unavailable=raw.unavailable,
                cleanup_failed=raw.cleanup_failed,
            )
            state = ExecutionState.FAILED
        results.append(_result_for(declaration, index, state, raw))
        if state is not ExecutionState.APPROVED:
            for later_index, later in enumerate(validators[index + 1 :], start=index + 1):
                results.append(_result_for(later, later_index, ExecutionState.NOT_RUN))
            break

    states = {result.status for result in results}
    if states <= {ExecutionState.APPROVED}:
        overall_status = OverallStatus.APPROVED
    elif states & {
        ExecutionState.UNAVAILABLE,
        ExecutionState.CLEANUP_FAILED,
    }:
        overall_status = OverallStatus.INCOMPLETE
    else:
        overall_status = OverallStatus.FAILED
    return ValidatorOutput(
        overall_passed=overall_status is OverallStatus.APPROVED,
        overall_status=overall_status,
        result_profile="v2",
        tools=results,
        run_id=run_id,
    )
