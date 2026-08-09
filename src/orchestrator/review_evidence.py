"""Pure contracts for attested independent-review evidence.

This module deliberately has no PatchForge pipeline, filesystem, network, or
GitHub dependency.  Workflows construct its inputs from trusted Git and
Actions metadata; callers must verify the external artifact attestation before
accepting a ``ReviewRecord``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewPhase(str, Enum):
    PLAN = "plan_review"
    DIFF = "diff_review"


class ReviewStatus(str, Enum):
    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"
    ADMISSION_REJECTED = "admission_rejected"


class ModelTier(str, Enum):
    HIGH_ASSURANCE = "high_assurance"
    ECONOMY = "economy"


class Severity(str, Enum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"
    INFORMATIONAL = "informational"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GitOperation(str, Enum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"
    RENAME = "rename"


class UnavailableReason(str, Enum):
    AUTHENTICATION = "authentication"
    MALFORMED_OUTPUT = "malformed_output"
    PROVIDER_FAILURE = "provider_failure"
    QUOTA = "quota"
    TIMEOUT = "timeout"


class AdmissionReason(str, Enum):
    DECLARATION_ABSENT = "declaration_absent"
    DECLARATION_UNREADABLE = "declaration_unreadable"
    DECLARATION_INVALID = "declaration_invalid"
    PLAN_TRANSITION_INVALID = "plan_transition_invalid"
    IMPLEMENTATION_CHAIN_INVALID = "implementation_chain_invalid"


class DeclarationProvenanceKind(str, Enum):
    VALIDATED = "validated"
    RAW = "raw"
    ABSENT = "absent"
    UNREADABLE = "unreadable"


def canonical_json(value: object) -> bytes:
    """Return the only serialization used for evidence and packet digests."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def sha256_digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(value)).hexdigest()}"


class AllowedOperationRule(_ClosedModel):
    pattern: str = Field(min_length=1)
    operations: tuple[GitOperation, ...] = Field(min_length=1)


class _PlanScope(_ClosedModel):
    allowed_path_patterns: tuple[str, ...] = Field(min_length=1)
    allowed_operations: tuple[AllowedOperationRule, ...] = Field(min_length=1)
    max_changed_files: int = Field(ge=0)
    max_changed_lines: int = Field(ge=0)

    @field_validator("allowed_operations", mode="before")
    @classmethod
    def _freeze_operations(cls, value: object) -> object:
        if isinstance(value, dict):
            return tuple(
                {"pattern": pattern, "operations": operations}
                for pattern, operations in value.items()
            )
        return value


class PlanScopeDeclaration(_PlanScope):
    """Untrusted in-tree declaration; trusted Git metadata is added by the harness."""


class PlanScopePacket(_PlanScope):
    base_sha: str = Field(min_length=1)
    plan_head_sha: str = Field(min_length=1)

    @classmethod
    def from_declaration(
        cls, declaration: PlanScopeDeclaration, *, base_sha: str, plan_head_sha: str
    ) -> "PlanScopePacket":
        return cls(
            base_sha=base_sha,
            plan_head_sha=plan_head_sha,
            **declaration.model_dump(mode="python"),
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class PlanReviewSubject(_ClosedModel):
    phase: Literal[ReviewPhase.PLAN] = ReviewPhase.PLAN
    base_sha: str = Field(min_length=1)
    plan_head_sha: str = Field(min_length=1)
    packet_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class DiffReviewSubject(_ClosedModel):
    phase: Literal[ReviewPhase.DIFF] = ReviewPhase.DIFF
    base_sha: str = Field(min_length=1)
    plan_head_sha: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    diff_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


ReviewSubject = PlanReviewSubject | DiffReviewSubject


class DeclarationProvenance(_ClosedModel):
    kind: DeclarationProvenanceKind
    digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_digest(self) -> "DeclarationProvenance":
        digest_required = self.kind in {
            DeclarationProvenanceKind.VALIDATED,
            DeclarationProvenanceKind.RAW,
        }
        if digest_required != (self.digest is not None):
            raise ValueError("declaration provenance digest must match its kind")
        return self


class PlanAdmissionSubject(_ClosedModel):
    phase: Literal[ReviewPhase.PLAN] = ReviewPhase.PLAN
    base_sha: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    declaration_provenance: DeclarationProvenance


class DiffAdmissionSubject(_ClosedModel):
    phase: Literal[ReviewPhase.DIFF] = ReviewPhase.DIFF
    base_sha: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    declaration_provenance: DeclarationProvenance


AdmissionSubject = PlanAdmissionSubject | DiffAdmissionSubject


class Finding(_ClosedModel):
    finding_id: str = Field(min_length=1)
    evidence_reference: str = Field(min_length=1)
    severity: Severity
    confidence: Confidence


class ReviewRecord(_ClosedModel):
    """The attested subject itself; it intentionally has no attestation reference."""

    schema_version: Literal["review-evidence@2"] = "review-evidence@2"
    record_id: str = Field(min_length=1)
    phase: ReviewPhase
    status: ReviewStatus
    workflow_name: str = Field(min_length=1)
    workflow_run_id: int = Field(gt=0)
    emitted_at: datetime
    model_tier: ModelTier | None = None
    subject: ReviewSubject | AdmissionSubject
    findings: tuple[Finding, ...] = ()
    unavailable_reason: UnavailableReason | None = None
    admission_reason: AdmissionReason | None = None

    @model_validator(mode="after")
    def _validate_status_payload(self) -> "ReviewRecord":
        if self.phase != self.subject.phase:
            raise ValueError("record phase must match subject phase")
        if self.status is ReviewStatus.COMPLETED:
            if not isinstance(self.subject, (PlanReviewSubject, DiffReviewSubject)):
                raise ValueError("completed records require an admitted subject")
            if self.model_tier is None:
                raise ValueError("completed records require model_tier")
            if self.unavailable_reason is not None or self.admission_reason is not None:
                raise ValueError("completed records cannot contain a reason code")
        elif self.status is ReviewStatus.UNAVAILABLE:
            if not isinstance(self.subject, (PlanReviewSubject, DiffReviewSubject)):
                raise ValueError("unavailable records require an admitted subject")
            if self.model_tier is None or self.unavailable_reason is None:
                raise ValueError("unavailable records require model_tier and unavailable_reason")
            if self.findings or self.admission_reason is not None:
                raise ValueError("unavailable records cannot contain findings or admission_reason")
        else:
            if not isinstance(self.subject, (PlanAdmissionSubject, DiffAdmissionSubject)):
                raise ValueError("admission rejections require a candidate subject")
            if self.model_tier is not None or self.findings or self.unavailable_reason is not None:
                raise ValueError(
                    "admission rejections cannot contain tier, findings, or unavailable_reason"
                )
            if self.admission_reason is None:
                raise ValueError("admission rejections require admission_reason")
            expected_provenance = {
                AdmissionReason.DECLARATION_ABSENT: DeclarationProvenanceKind.ABSENT,
                AdmissionReason.DECLARATION_UNREADABLE: DeclarationProvenanceKind.UNREADABLE,
                AdmissionReason.DECLARATION_INVALID: DeclarationProvenanceKind.RAW,
            }.get(self.admission_reason)
            if (
                expected_provenance is not None
                and self.subject.declaration_provenance.kind is not expected_provenance
            ):
                raise ValueError("declaration rejection reason must match declaration provenance")
        return self

    @property
    def digest(self) -> str:
        """Digest of the exact bytes the workflow submits to ``actions/attest``."""
        return sha256_digest(self)

    @property
    def subject_digest(self) -> str:
        return sha256_digest(self.subject)

    def artifact_name(self, pull_request_number: int) -> str:
        if pull_request_number <= 0:
            raise ValueError("pull_request_number must be positive")
        subject_hex = self.subject_digest.removeprefix("sha256:")
        return (
            f"review-evidence-{self.phase.value}-{pull_request_number}-"
            f"sha256-{subject_hex}-{self.workflow_run_id}"
        )


def parse_review_record(value: bytes | str | dict[str, object]) -> ReviewRecord:
    """Dispatch evidence records by version before interpreting their payload."""
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    if value.get("schema_version") != "review-evidence@2":
        raise ValueError("unsupported review evidence schema version")
    return ReviewRecord.model_validate(value)


class ChangedPath(_ClosedModel):
    operation: GitOperation
    path: str | None = None
    previous_path: str | None = None
    changed_lines: int | None = Field(default=None, ge=0)
    is_binary: bool = False
    is_submodule: bool = False

    @model_validator(mode="after")
    def _validate_paths(self) -> "ChangedPath":
        if self.operation is GitOperation.RENAME:
            if not self.path or not self.previous_path:
                raise ValueError("rename requires path and previous_path")
        elif not self.path or self.previous_path is not None:
            raise ValueError("non-rename requires only path")
        return self


class MechanicalScopeViolation(_ClosedModel):
    kind: Literal[
        "base_sha_mismatch",
        "path_outside_allowed_patterns",
        "operation_not_allowed",
        "changed_file_budget_exceeded",
        "changed_line_budget_exceeded",
    ]
    path: str | None = None


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def evaluate_mechanical_scope(
    packet: PlanScopePacket, *, current_base_sha: str, changes: tuple[ChangedPath, ...]
) -> tuple[MechanicalScopeViolation, ...]:
    """Return only the four ADR-authorized mechanical failure categories."""
    violations: list[MechanicalScopeViolation] = []
    if current_base_sha != packet.base_sha:
        violations.append(MechanicalScopeViolation(kind="base_sha_mismatch"))
    if len(changes) > packet.max_changed_files:
        violations.append(MechanicalScopeViolation(kind="changed_file_budget_exceeded"))
    line_count = 0
    for change in changes:
        paths = (
            (change.previous_path, change.path)
            if change.operation is GitOperation.RENAME
            else (change.path,)
        )
        for path in paths:
            assert path is not None
            if not _matches(path, packet.allowed_path_patterns):
                violations.append(
                    MechanicalScopeViolation(kind="path_outside_allowed_patterns", path=path)
                )
            operations = tuple(
                operation
                for rule in packet.allowed_operations
                if fnmatch.fnmatchcase(path, rule.pattern)
                for operation in rule.operations
            )
            if change.operation not in operations:
                violations.append(MechanicalScopeViolation(kind="operation_not_allowed", path=path))
        if change.is_binary or change.is_submodule or change.changed_lines is None:
            line_count = packet.max_changed_lines + 1
        else:
            line_count += change.changed_lines
    if line_count > packet.max_changed_lines:
        violations.append(MechanicalScopeViolation(kind="changed_line_budget_exceeded"))
    return tuple(violations)


class CommitTransition(_ClosedModel):
    sha: str = Field(min_length=1)
    parents: tuple[str, ...]
    changed_paths: tuple[str, ...] = ()


def validate_linear_admission(
    *,
    base_sha: str,
    plan_head_sha: str,
    plan_commit: CommitTransition,
    post_plan: tuple[CommitTransition, ...],
) -> tuple[str, ...]:
    """Validate the complete admitted chain, not merely a net tree diff."""
    errors: list[str] = []
    if plan_commit.sha != plan_head_sha:
        errors.append("plan commit sha does not match plan_head_sha")
    if plan_commit.parents != (base_sha,):
        errors.append("plan commit must have base_sha as its only parent")
    if set(plan_commit.changed_paths) != {".patchforge/review-plan.json"}:
        errors.append("plan commit may modify only .patchforge/review-plan.json")
    expected_parent = plan_head_sha
    for commit in post_plan:
        if len(commit.parents) != 1 or commit.parents[0] != expected_parent:
            errors.append("post-plan commits must form one linear chain from plan_head_sha")
            break
        expected_parent = commit.sha
    return tuple(errors)
