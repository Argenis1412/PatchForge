from typing import List

from pydantic import BaseModel, Field


class Task(BaseModel):
    task_id: str = Field(..., description="Unique identifier for the task, e.g., 'T1', 'T2'")
    title: str = Field(..., description="Short title of the task")
    description: str = Field(..., description="Detailed description of what needs to be done")
    files_to_modify: List[str] = Field(..., description="List of files that will be affected")
    priority: str = Field(..., description="'high', 'medium', or 'low'")
    effort: str = Field(..., description="'high', 'medium', or 'low'")
    risk_level: str = Field(..., description="'high', 'medium', or 'low'")
    dependencies: List[str] = Field(default=[], description="List of task_ids that block this task")

class ArchitectOutput(BaseModel):
    validated_findings: List[str] = Field(..., description="Findings from the Scout that have been validated as true positives")
    false_positives: List[str] = Field(..., description="Findings from the Scout that are likely false positives or not worth the effort")
    systemic_risks: List[str] = Field(..., description="Systemic risks not caught by the Scout")
    implementation_plan: List[Task] = Field(..., description="Ordered list of tasks for implementation")
    blockers: List[str] = Field(..., description="Items blocking Phase 2 of the Engineering Playbook")
