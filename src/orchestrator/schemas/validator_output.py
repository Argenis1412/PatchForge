"""
schemas/validator_output.py
Output contract of the Validator. Defines the result of each tool and the global summary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    tool: Literal["ruff", "pytest", "tsc"]
    passed: bool
    return_code: int
    stdout: str = ""
    stderr: str = ""
    error_summary: str | None = None  # Gemini fills this only if passed == False


class ValidatorOutput(BaseModel):
    overall_passed: bool
    tools: list[ToolResult] = Field(default_factory=list)
    llm_summary: str | None = None  # global summary if at least one tool failed
    run_id: str = ""
    model_used_for_summary: str = ""  # empty if all passed (Gemini was not invoked)
