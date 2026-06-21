"""Risk gate result model."""

__all__ = [
    "RISK_GATE_JSON",
    "RiskGateResult",
]

from pydantic import BaseModel

RISK_GATE_JSON = "risk_gate.json"


class RiskGateResult(BaseModel):
    passed: bool
    gate: str
    reasons: list[str]
