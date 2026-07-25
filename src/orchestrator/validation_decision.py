"""Central authorization policy for validator results.

Persisted decisions are audit evidence only. Mutating flows evaluate the fresh
``ValidatorOutput`` produced in their own process and never reuse a prior
``validation.json`` as a cross-process credential.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.schemas.validator_output import (
    AuthorizationProfile,
    CoverageStatus,
    DecisionReason,
    OverallStatus,
    ValidationDecision,
    ValidationRequirements,
    ValidationSubject,
    ValidatorOutput,
)

if TYPE_CHECKING:
    from orchestrator.schemas.config import TargetConfig


def _requirements_for(config: "TargetConfig") -> ValidationRequirements:
    validators = config.validators or []
    payload = [validator.model_dump(mode="json") for validator in validators]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    roles = sorted({role for item in payload for role in item["roles"] or []})
    return ValidationRequirements(
        roles=roles,
        validator_ids=[item["id"] for item in payload],
        digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def expected_validation_subject(
    *, run_id: str, project_root: Path, base_commit: str, patch_checksum: str
) -> ValidationSubject:
    """Build the caller-authoritative identity for fresh validation evidence."""
    return ValidationSubject(
        run_id=run_id,
        project_identity=str(project_root.resolve()),
        base_commit=base_commit,
        patch_checksum=patch_checksum,
    )


def evaluate_validation(
    output: ValidatorOutput,
    *,
    fresh: bool = False,
    expected_subject: ValidationSubject | None = None,
) -> ValidationDecision:
    """Evaluate validation evidence according to its explicit policy.

    ``fresh`` is reserved for a result returned directly by the validator in
    the current process. It preserves the legacy in-memory transition without
    making an unversioned artifact reusable after persistence.
    """
    if getattr(output, "schema_version", None) != 2:
        if not fresh:
            return ValidationDecision(
                authorized=False, reasons=[DecisionReason.UNSUPPORTED_ARTIFACT]
            )
        reason = DecisionReason.APPROVED if output.overall_passed else DecisionReason.LEGACY_FAILED
        return ValidationDecision(authorized=output.overall_passed, reasons=[reason])
    if (
        getattr(output, "authorization_profile", None) is None
        or getattr(output, "validation_requirements", None) is None
        or getattr(output, "validation_subject", None) is None
    ):
        return ValidationDecision(authorized=False, reasons=[DecisionReason.UNSUPPORTED_ARTIFACT])
    if expected_subject is not None and output.validation_subject != expected_subject:
        return ValidationDecision(authorized=False, reasons=[DecisionReason.UNSUPPORTED_ARTIFACT])
    if output.authorization_profile is AuthorizationProfile.LEGACY_V1_COMPAT:
        reason = DecisionReason.APPROVED if output.overall_passed else DecisionReason.LEGACY_FAILED
        return ValidationDecision(authorized=output.overall_passed, reasons=[reason])
    if output.overall_status is not OverallStatus.APPROVED:
        return ValidationDecision(authorized=False, reasons=[DecisionReason.OVERALL_NOT_APPROVED])
    verified_roles = {
        role
        for tool in output.tools
        for role, coverage in tool.role_coverage.items()
        if coverage is CoverageStatus.VERIFIED
    }
    missing = set(output.validation_requirements.roles) - verified_roles
    if missing:
        return ValidationDecision(authorized=False, reasons=[DecisionReason.ROLE_NOT_VERIFIED])
    return ValidationDecision(authorized=True, reasons=[DecisionReason.APPROVED])


def attach_validation_decision(output: ValidatorOutput, config: "TargetConfig") -> ValidatorOutput:
    """Version a newly produced result and attach its derived audit decision."""
    profile = (
        AuthorizationProfile.V2_VERIFIED_ROLES
        if config.validators is not None
        else AuthorizationProfile.LEGACY_V1_COMPAT
    )
    versioned = output.model_copy(
        update={
            "schema_version": 2,
            "authorization_profile": profile,
            "validation_requirements": _requirements_for(config),
            "validation_subject": ValidationSubject(
                run_id=output.run_id,
                project_identity=str(config.target_path.resolve()),
            ),
        }
    )
    return versioned.model_copy(update={"decision": evaluate_validation(versioned)})


def bind_validation_subject(
    output: ValidatorOutput, *, base_commit: str, patch_checksum: str
) -> ValidatorOutput:
    """Bind fresh validation evidence to the candidate handled by a caller."""
    if not isinstance(output, ValidatorOutput) or output.validation_subject is None:
        return output
    subject = output.validation_subject
    updates = {}
    if subject.base_commit is None:
        updates["base_commit"] = base_commit
    if subject.patch_checksum is None:
        updates["patch_checksum"] = patch_checksum
    subject = subject.model_copy(update=updates)
    bound = output.model_copy(update={"validation_subject": subject})
    return bound.model_copy(update={"decision": evaluate_validation(bound)})
