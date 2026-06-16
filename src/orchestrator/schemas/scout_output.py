"""Output contract for the Scout agent."""

__all__ = [
    "Hotspot",
    "ScoutOutput",
]

from typing import Literal

from pydantic import BaseModel


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
