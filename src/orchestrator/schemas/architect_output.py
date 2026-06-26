"""Output contract for the Architect agent."""

__all__ = [
    "ArchitectOutput",
    "Task",
]

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class Task(BaseModel):
    task_id: str = Field(..., description="Unique identifier for the task, e.g., 'T1', 'T2'")
    title: str = Field(..., description="Short title of the task")
    description: str = Field(..., description="Detailed description of what needs to be done")
    files_to_modify: List[str] = Field(..., description="List of files that will be affected")
    priority: Literal["low", "medium", "high"] = Field(
        ..., description="'high', 'medium', or 'low'"
    )
    effort: Literal["low", "medium", "high"] = Field(..., description="'high', 'medium', or 'low'")
    risk_level: Literal["low", "medium", "high"] = Field(
        ..., description="'high', 'medium', or 'low'"
    )
    dependencies: List[str] = Field(default=[], description="List of task_ids that block this task")
    reason: Optional[str] = Field(
        default=None, description="Why this task is necessary given the findings."
    )
    risk_reasons: Optional[List[str]] = Field(
        default=None, description="Specific reasons this task carries its stated risk level."
    )
    validation_expectations: Optional[List[str]] = Field(
        default=None,
        description="Observable outcomes that confirm the task was applied correctly.",
    )
    status: Literal["blocked"] | None = Field(
        default=None,
        description=(
            "Set to 'blocked' by plan.py when risk_level is 'high'. "
            "None means the task was not individually evaluated. "
            "Plan-level approval is determined by the plan gate, not this field."
        ),
    )


class ArchitectOutput(BaseModel):
    validated_findings: List[str] = Field(
        ..., description="Findings from the Scout that have been validated as true positives"
    )
    false_positives: List[str] = Field(
        ...,
        description=(
            "Findings from the Scout that are likely false positives or not worth the effort"
        ),
    )
    systemic_risks: List[str] = Field(..., description="Systemic risks not caught by the Scout")
    implementation_plan: List[Task] = Field(
        ..., description="Ordered list of tasks for implementation"
    )
    blockers: List[str] = Field(
        ..., description="Items blocking Phase 2 of the Engineering Playbook"
    )
