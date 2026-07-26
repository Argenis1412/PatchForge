"""Validated execution-plan contracts.

``plan.json`` remains the architect's proposal.  This module defines the
separate, deterministic contract that is allowed to reach the executor.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanViolation(BaseModel):
    """One stable, actionable execution-plan contract violation."""

    task_fingerprint: str
    field: str
    code: str
    message: str
    value: str | None = None


class FileEditOperation(BaseModel):
    """A deterministic, complete-file replacement operation.

    The executor can apply this operation without interpreting a task's
    natural-language description.  ``expected_sha256`` binds the replacement
    to the exact source content it was compiled against.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["file_edit"]
    path: str
    expected_sha256: str | None = None
    content: str


class ExecutionTask(BaseModel):
    """A task that already contains its executable mutation."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    operation: FileEditOperation


class ExecutablePlanV2(BaseModel):
    """The only plan shape accepted by the deterministic executor."""

    model_config = ConfigDict(extra="forbid")

    plan_contract_version: Literal[2]
    base_commit: str
    tasks: list[ExecutionTask] = Field(min_length=1)


class ExecutionPlanContractError(Exception):
    """A deterministic, non-retryable execution-plan rejection."""

    category = "contract_violation"
    retryable = False

    def __init__(self, violations: list[PlanViolation]) -> None:
        self.violations = sorted(
            violations,
            key=lambda item: (item.task_fingerprint, item.field, item.code, item.value or ""),
        )
        super().__init__("execution plan contract is invalid")

    def model_dump(self) -> dict[str, object]:
        return {
            "code": "execution_plan_contract_invalid",
            "category": self.category,
            "retryable": self.retryable,
            "violations": [violation.model_dump() for violation in self.violations],
        }


class MutationPreconditionError(Exception):
    """A compiled mutation no longer matches its immutable source base."""

    category = "mutation_precondition_conflict"
    retryable = False
    requires_replan = True


def task_fingerprint(task: object) -> str:
    """Return a deterministic diagnostic fingerprint for a task-like value."""
    if isinstance(task, BaseModel):
        payload = task.model_dump_json(by_alias=True, exclude_none=False)
    else:
        payload = repr(task)
    return sha256(payload.encode("utf-8")).hexdigest()
