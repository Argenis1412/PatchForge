"""
schemas/validator_output.py
Contrato de salida del Validator. Define el resultado de cada tool y el summary global.
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
    error_summary: str | None = None   # Gemini lo rellena solo si passed == False


class ValidatorOutput(BaseModel):
    overall_passed: bool
    tools: list[ToolResult] = Field(default_factory=list)
    llm_summary: str | None = None     # resumen global si hay al menos un fallo
    run_id: str = ""
    model_used_for_summary: str = ""   # vacío si todo pasó (Gemini no fue invocado)
