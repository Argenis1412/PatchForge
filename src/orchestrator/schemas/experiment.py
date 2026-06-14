from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Verdict(BaseModel):
    run_id: str
    status: Literal["passed", "failed"]
    validation_passed: bool
    apply_succeeded: bool
    error_message: str | None = None
    generated_at: datetime
