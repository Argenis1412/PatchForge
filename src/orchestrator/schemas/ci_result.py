"""Versioned CI pipeline results and their safe external decoder."""

from __future__ import annotations

__all__ = [
    "CiCommandResult",
    "CiPreflightRejectedResult",
    "CiResult",
    "github_output_lines",
    "parse_ci_result",
]

import json
from typing import Any, List, Literal, Optional, TypeAlias

from pydantic import BaseModel


class CiResult(BaseModel):
    run_id: str
    branch: str
    status: Literal[
        "applied",
        "scan_failed",
        "plan_failed",
        "preview_failed",
        "apply_failed",
    ]
    risk_budget: str
    affected_files: List[str]
    validation_passed: bool
    error: Optional[str] = None
    issue_number: Optional[int] = None
    force_provider: Optional[str] = None
    triggered_by: Optional[str] = None
    approved_by: Optional[str] = None


class CiPreflightRejectedResult(BaseModel):
    """The ADR-0014 result emitted before any CI stage begins."""

    schema_version: Literal["ci_result@2"] = "ci_result@2"
    run_id: Literal[""] = ""
    branch: Literal[""] = ""
    status: Literal["preflight_rejected"] = "preflight_rejected"
    risk_budget: str
    affected_files: List[str] = []
    validation_passed: Literal[False] = False
    error: str
    issue_number: Optional[int] = None
    force_provider: Optional[str] = None
    triggered_by: Optional[str] = None
    approved_by: Optional[str] = None
    preflight_stage: Literal["architect"] = "architect"
    preflight_reason: Literal[
        "credential_source_rejected",
        "provider_policy_unavailable",
        "provider_policy_rejected",
        "eligibility_evaluation_failed",
        "no_eligible_provider",
    ]


CiCommandResult: TypeAlias = CiResult | CiPreflightRejectedResult


def parse_ci_result(content: str | bytes | dict[str, Any]) -> CiCommandResult:
    """Decode a CI result by version before inspecting its status."""
    try:
        raw = json.loads(content) if isinstance(content, (str, bytes)) else content
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("CI result is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise ValueError("CI result must be a JSON object.")
    if "schema_version" not in raw:
        return CiResult.model_validate(raw)
    if raw["schema_version"] == "ci_result@2":
        return CiPreflightRejectedResult.model_validate(raw)
    raise ValueError("CI result has an unsupported schema version.")


def github_output_lines(result: CiCommandResult) -> list[str]:
    """Return one-line GitHub Actions outputs from a validated result only."""
    preflight_reason = (
        result.preflight_reason if isinstance(result, CiPreflightRejectedResult) else ""
    )
    schema_version = result.schema_version if isinstance(result, CiPreflightRejectedResult) else ""
    raw_fields = {
        "schema_version": schema_version,
        "run_id": result.run_id,
        "branch": result.branch,
        "status": result.status,
        "risk": result.risk_budget,
        "preflight_reason": preflight_reason,
    }
    for name, value in raw_fields.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"CI result output field {name!r} must be single-line.")
    return [
        *(f"{name}={value}" for name, value in raw_fields.items()),
        f"error_json={json.dumps(result.error or '')}",
        f"affected_files_json={json.dumps(result.affected_files)}",
        f"triggered_by_json={json.dumps(result.triggered_by or '')}",
    ]
