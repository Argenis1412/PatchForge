from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class Verdict(BaseModel):
    run_id: str
    status: Literal["passed", "failed"]
    validation_passed: bool
    apply_succeeded: bool
    error_message: str | None = None
    generated_at: datetime


def write_verdict(run_dir: Path, verdict: Verdict) -> None:
    """Write verdict.json and verdict.md to run_dir.

    Raises FileNotFoundError if run_dir does not exist.
    Does not create directories — the run directory must already exist.
    """
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    json_path = run_dir / "verdict.json"
    json_path.write_text(verdict.model_dump_json(indent=2), encoding="utf-8")

    md_path = run_dir / "verdict.md"
    _write_verdict_markdown(md_path, verdict)


def _write_verdict_markdown(path: Path, verdict: Verdict) -> None:
    lines = [
        "# Verdict",
        "",
        f"- **Run ID:** {verdict.run_id}",
        f"- **Status:** {verdict.status}",
        f"- **Validation passed:** {verdict.validation_passed}",
        f"- **Apply succeeded:** {verdict.apply_succeeded}",
    ]
    if verdict.error_message is not None:
        lines.append(f"- **Error:** {verdict.error_message}")
    lines.extend(
        [
            f"- **Generated at:** {verdict.generated_at.isoformat()}",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
