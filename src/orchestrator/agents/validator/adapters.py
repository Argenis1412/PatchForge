"""V2 validator declaration execution and aggregation.

Legacy runners intentionally remain separate: their pass semantics are part of
the V1 contract. This module evaluates raw process results against one V2
declaration at a time.
"""

from __future__ import annotations

import sys
from pathlib import Path

from orchestrator.schemas.config import ValidatorConfig
from orchestrator.schemas.validator_output import (
    CoverageStatus,
    ExecutionState,
    OverallStatus,
    ToolResult,
    ValidatorOutput,
)

from .process import ProcessResult, build_venv_environment, execute_process, prepare_process

_STANDARD_COMMANDS: dict[str, list[str]] = {
    "ruff": [sys.executable, "-m", "ruff", "check", "."],
    "pytest": [sys.executable, "-m", "pytest", ".", "--tb=short", "-q"],
    "tsc": ["npx", "tsc", "--noEmit"],
}


def _has_frontend(project_root: Path) -> bool:
    return any("node_modules" not in path.parts for path in project_root.rglob("package.json"))


def _raw_result(declaration: ValidatorConfig, project_root: Path, timeout: int) -> ProcessResult:
    if declaration.adapter == "tsc" and not _has_frontend(project_root):
        return ProcessResult(return_code=None, unavailable=True)
    command = declaration.command or _STANDARD_COMMANDS.get(declaration.adapter)
    if command is None:
        return ProcessResult(return_code=None, unavailable=True)
    env = build_venv_environment(project_root) if not Path(command[0]).is_absolute() else None
    return execute_process(prepare_process(command, project_root, environment=env), timeout)


def _coverage(declaration: ValidatorConfig, state: ExecutionState) -> dict[str, CoverageStatus]:
    roles = [role.value for role in declaration.roles or []]
    if state is not ExecutionState.APPROVED:
        return dict.fromkeys(roles, CoverageStatus.ABSENT)
    declared_only = declaration.adapter in {"command", "tox"} or declaration.command is not None
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
    results: list[ToolResult] = []
    for index, declaration in enumerate(validators):
        raw = _raw_result(declaration, project_root, timeout)
        state = _terminal_state(raw, declaration)
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
        ExecutionState.NOT_RUN,
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
