"""Risk gate result model."""

__all__ = [
    "RiskGateResult",
]

from pydantic import BaseModel


class RiskGateResult(BaseModel):
    passed: bool
    gate: str
    reasons: list[str]
