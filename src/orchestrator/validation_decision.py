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
    ValidationPolicy,
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


def validation_policy_for(config: "TargetConfig") -> ValidationPolicy:
    """Return the path-independent authorization policy for *config*."""
    validators = [validator.model_dump(mode="json") for validator in config.validators or []]
    payload = {
        "schema_version": config.schema_version,
        "validators": validators,
        "lint_command": config.lint_command,
        "test_command": config.test_command,
        "typecheck_command": config.typecheck_command,
        "supports_tests": config.capabilities.effective_supports_tests,
        "validator_timeout": config.validator_timeout,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return ValidationPolicy(**payload, digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest())


def expected_validation_subject(
    *,
    run_id: str,
    project_root: Path,
    base_commit: str,
    patch_checksum: str,
    candidate_commit: str | None = None,
    repository_identity: str | None = None,
    policy_digest: str | None = None,
) -> ValidationSubject:
    """Build the caller-authoritative identity for fresh validation evidence."""
    return ValidationSubject(
        run_id=run_id,
        project_identity=str(project_root.resolve()),
        base_commit=base_commit,
        patch_checksum=patch_checksum,
        candidate_commit=candidate_commit,
        repository_identity=repository_identity,
        policy_digest=policy_digest,
    )


def evaluate_validation(
    output: ValidatorOutput,
    *,
    fresh: bool = False,
    expected_subject: ValidationSubject | None = None,
    expected_policy: ValidationPolicy | None = None,
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
    if expected_policy is not None:
        if output.validation_subject.policy_digest != expected_policy.digest:
            return ValidationDecision(
                authorized=False, reasons=[DecisionReason.UNSUPPORTED_ARTIFACT]
            )
        if output.validation_requirements != _requirements_for_policy(expected_policy):
            return ValidationDecision(
                authorized=False, reasons=[DecisionReason.UNSUPPORTED_ARTIFACT]
            )
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


def _requirements_for_policy(policy: ValidationPolicy) -> ValidationRequirements:
    payload = policy.validators
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    roles = sorted({role for item in payload for role in item.get("roles", []) or []})
    return ValidationRequirements(
        roles=roles,
        validator_ids=[item["id"] for item in payload],
        digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def attach_validation_decision(
    output: ValidatorOutput,
    config: "TargetConfig",
    *,
    policy: ValidationPolicy | None = None,
    subject: ValidationSubject | None = None,
) -> ValidatorOutput:
    """Version a newly produced result and attach its derived audit decision."""
    profile = (
        AuthorizationProfile.V2_VERIFIED_ROLES
        if config.validators is not None
        else AuthorizationProfile.LEGACY_V1_COMPAT
    )
    policy = policy or validation_policy_for(config)
    versioned = output.model_copy(
        update={
            "schema_version": 2,
            "authorization_profile": profile,
            "validation_requirements": _requirements_for_policy(policy),
            "validation_subject": subject
            or ValidationSubject(
                run_id=output.run_id,
                project_identity=str(config.target_path.resolve()),
                policy_digest=policy.digest,
            ),
        }
    )
    return versioned.model_copy(
        update={"decision": evaluate_validation(versioned, expected_policy=policy)}
    )


def bind_validation_subject(
    output: ValidatorOutput,
    *,
    base_commit: str,
    patch_checksum: str,
    candidate_commit: str | None = None,
    repository_identity: str | None = None,
    policy_digest: str | None = None,
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
    if candidate_commit is not None:
        updates["candidate_commit"] = candidate_commit
    if repository_identity is not None:
        updates["repository_identity"] = repository_identity
    if policy_digest is not None:
        updates["policy_digest"] = policy_digest
    subject = subject.model_copy(update=updates)
    bound = output.model_copy(update={"validation_subject": subject})
    return bound.model_copy(update={"decision": evaluate_validation(bound)})
