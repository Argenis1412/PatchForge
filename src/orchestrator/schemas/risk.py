from pydantic import BaseModel


class RiskGateResult(BaseModel):
    passed: bool
    gate: str
    reasons: list[str]
