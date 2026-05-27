from pydantic import BaseModel
from typing import Literal


class Hotspot(BaseModel):
    file: str
    issue: str
    severity: Literal["low", "medium", "high"]
    risk_level: Literal["low", "medium", "high"]
    dependencies: list[str]


class ScoutOutput(BaseModel):
    hotspots: list[Hotspot]
    recommended_order: list[str]
    risks: list[str]
    summary: str
