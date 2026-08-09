"""Deterministic contracts for attested independent-review evidence v3.

This module is deliberately pure: GitHub discovery, artifact download and
attestation verification are adapters.  A caller must not turn an artifact
into evidence until it has supplied a verified provenance receipt.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def canonical_json(value: object) -> bytes:
    """Return the sole JSON serialization used by packets and records."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)

    def _serialize_nested(item: object) -> object:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json", exclude_none=False)
        raise TypeError(f"cannot canonically serialize {type(item).__name__}")

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_serialize_nested,
    ).encode("utf-8")


def sha256_digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(value)).hexdigest()}"


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
                {"pattern": key, "operations": operations} for key, operations in value.items()
            )
        return value


class PlanScopeDeclaration(_PlanScope):
    """Untrusted declaration; only a trusted harness adds Git identities."""


class PlanScopePacket(_PlanScope):
    base_sha: str = Field(min_length=1)
    plan_head_sha: str = Field(min_length=1)

    @classmethod
    def from_declaration(
        cls, declaration: PlanScopeDeclaration, *, base_sha: str, plan_head_sha: str
    ) -> "PlanScopePacket":
        return cls(
            base_sha=base_sha, plan_head_sha=plan_head_sha, **declaration.model_dump(mode="python")
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class TreeEntry(_ClosedModel):
    path: str = Field(min_length=1)
    object_type: Literal["blob", "commit", "tree"]
    mode: str = Field(min_length=1)
    object_id: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def _path_is_utf8(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("tree path is not UTF-8 representable") from error
        return value


class TextDelta(_ClosedModel):
    path: str
    old_text: str | None
    new_text: str | None


class CanonicalChange(_ClosedModel):
    operation: GitOperation
    path: str
    previous_path: str | None = None
    old_entry: TreeEntry | None = None
    new_entry: TreeEntry | None = None
    changed_lines: int | None = Field(default=None, ge=0)
    non_text_reason: Literal["binary", "invalid_utf8", "submodule"] | None = None

    @model_validator(mode="after")
    def _validate_change(self) -> "CanonicalChange":
        if self.operation is GitOperation.ADD and self.old_entry is not None:
            raise ValueError("add cannot contain old_entry")
        if self.operation is GitOperation.DELETE and self.new_entry is not None:
            raise ValueError("delete cannot contain new_entry")
        if self.operation is GitOperation.RENAME:
            if self.previous_path is None:
                raise ValueError("rename requires previous_path")
        elif self.previous_path is not None:
            raise ValueError("only rename has previous_path")
        if self.non_text_reason is not None and self.changed_lines is not None:
            raise ValueError("non-text changes have no line count")
        return self


class CanonicalChangeSet(_ClosedModel):
    schema_version: Literal["canonical-change-set@1"] = "canonical-change-set@1"
    plan_tree: tuple[TreeEntry, ...]
    head_tree: tuple[TreeEntry, ...]
    changes: tuple[CanonicalChange, ...]

    @property
    def digest(self) -> str:
        return sha256_digest(self)


def _physical_lines(value: str) -> int:
    if not value:
        return 0
    return value.count("\n") + (0 if value.endswith("\n") else 1)


def _text_or_reason(
    entry: TreeEntry | None, blobs: Mapping[str, bytes]
) -> tuple[str | None, str | None]:
    if entry is None:
        return None, None
    if entry.object_type == "commit":
        return None, "submodule"
    if entry.object_type != "blob":
        return None, "binary"
    raw = blobs.get(entry.object_id)
    if raw is None:
        raise ValueError("missing blob bytes")
    if b"\0" in raw:
        return None, "binary"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "invalid_utf8"


def build_canonical_change_set(
    plan_tree: Sequence[TreeEntry], head_tree: Sequence[TreeEntry], blobs: Mapping[str, bytes]
) -> tuple[CanonicalChangeSet, tuple[TextDelta, ...]]:
    """Derive ADR-0015 operations from exact tree entries, never Git heuristics."""
    plan = {entry.path: entry for entry in plan_tree}
    head = {entry.path: entry for entry in head_tree}
    if len(plan) != len(plan_tree) or len(head) != len(head_tree):
        raise ValueError("tree contains duplicate paths")
    ordered_paths = sorted(set(plan) | set(head), key=lambda path: path.encode("utf-8"))
    removed = [plan[path] for path in ordered_paths if path in plan and path not in head]
    added = [head[path] for path in ordered_paths if path in head and path not in plan]
    removed_by_blob: dict[tuple[str, str], list[TreeEntry]] = defaultdict(list)
    added_by_blob: dict[tuple[str, str], list[TreeEntry]] = defaultdict(list)
    for entry in removed:
        if entry.object_type == "blob":
            removed_by_blob[(entry.object_id, entry.mode)].append(entry)
    for entry in added:
        if entry.object_type == "blob":
            added_by_blob[(entry.object_id, entry.mode)].append(entry)
    renames = {
        old.path: new
        for key, old_values in removed_by_blob.items()
        for new_values in [added_by_blob.get(key, [])]
        if len(old_values) == len(new_values) == 1
        for old in old_values
        for new in new_values
    }
    renamed_new = {entry.path for entry in renames.values()}
    changes: list[CanonicalChange] = []
    texts: list[TextDelta] = []
    for path in ordered_paths:
        old, new = plan.get(path), head.get(path)
        if old is not None and new is not None:
            if old == new:
                continue
            operation = GitOperation.MODIFY
        elif old is not None:
            if path in renames:
                new = renames[path]
                operation = GitOperation.RENAME
            else:
                operation = GitOperation.DELETE
        else:
            assert new is not None
            if path in renamed_new:
                continue
            operation = GitOperation.ADD
        old_text, old_reason = _text_or_reason(old, blobs)
        new_text, new_reason = _text_or_reason(new, blobs)
        reason = old_reason or new_reason
        lines = (
            None if reason else _physical_lines(old_text or "") + _physical_lines(new_text or "")
        )
        change = CanonicalChange(
            operation=operation,
            path=new.path if operation is GitOperation.RENAME else path,
            previous_path=old.path if operation is GitOperation.RENAME else None,
            old_entry=old,
            new_entry=new,
            changed_lines=lines,
            non_text_reason=reason,
        )
        changes.append(change)
        if reason is None:
            texts.append(TextDelta(path=change.path, old_text=old_text, new_text=new_text))
    return (
        CanonicalChangeSet(
            plan_tree=tuple(sorted(plan_tree, key=lambda entry: entry.path.encode("utf-8"))),
            head_tree=tuple(sorted(head_tree, key=lambda entry: entry.path.encode("utf-8"))),
            changes=tuple(changes),
        ),
        tuple(texts),
    )


class DiffReviewPacket(_ClosedModel):
    canonical_change_set: CanonicalChangeSet
    text_deltas: tuple[TextDelta, ...]

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


class AdmissionSubject(_ClosedModel):
    phase: ReviewPhase
    base_sha: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)


ReviewSubject = PlanReviewSubject | DiffReviewSubject | AdmissionSubject


class Finding(_ClosedModel):
    finding_id: str = Field(min_length=1)
    evidence_reference: str = Field(min_length=1)
    severity: Severity
    confidence: Confidence


class ReviewRecord(_ClosedModel):
    schema_version: Literal["review-evidence@3"] = "review-evidence@3"
    record_id: str = Field(min_length=1)
    phase: ReviewPhase
    status: ReviewStatus
    workflow_name: str = Field(min_length=1)
    workflow_run_id: int = Field(gt=0)
    emitted_at: datetime
    model_tier: ModelTier | None = None
    subject: ReviewSubject
    findings: tuple[Finding, ...] = ()
    unavailable_reason: UnavailableReason | None = None
    admission_reason: AdmissionReason | None = None

    @model_validator(mode="after")
    def _validate_status(self) -> "ReviewRecord":
        if self.phase != self.subject.phase:
            raise ValueError("record phase must match subject phase")
        if self.record_id != f"{self.workflow_run_id}:{self.phase.value}":
            raise ValueError("record_id must be derived from workflow_run_id and phase")
        admitted = isinstance(self.subject, (PlanReviewSubject, DiffReviewSubject))
        if self.status is ReviewStatus.COMPLETED:
            if (
                not admitted
                or self.model_tier is None
                or self.unavailable_reason
                or self.admission_reason
            ):
                raise ValueError("completed record has invalid payload")
        elif self.status is ReviewStatus.UNAVAILABLE:
            if (
                not admitted
                or self.model_tier is None
                or self.unavailable_reason is None
                or self.findings
                or self.admission_reason
            ):
                raise ValueError("unavailable record has invalid payload")
        elif (
            admitted
            or self.model_tier is not None
            or self.findings
            or self.unavailable_reason
            or self.admission_reason is None
        ):
            raise ValueError("admission rejection has invalid payload")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)

    @property
    def subject_digest(self) -> str:
        return sha256_digest(self.subject)

    def artifact_name(self, pull_request_number: int) -> str:
        if pull_request_number <= 0:
            raise ValueError("pull_request_number must be positive")
        subject_hex = self.subject_digest.removeprefix("sha256:")
        return (
            f"review-evidence-{self.phase.value}-{pull_request_number}-sha256-"
            f"{subject_hex}-{self.workflow_run_id}"
        )


def parse_review_record(value: object) -> ReviewRecord:
    """Version dispatch precedes every status or subject interpretation."""
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise ValueError("review evidence must be a JSON object")
    if value.get("schema_version") != "review-evidence@3":
        raise ValueError("unsupported review evidence schema version")
    return ReviewRecord.model_validate(value)


class MechanicalScopeViolation(_ClosedModel):
    kind: Literal[
        "base_sha_mismatch",
        "path_outside_allowed_patterns",
        "operation_not_allowed",
        "changed_file_budget_exceeded",
        "changed_line_budget_exceeded",
    ]
    path: str | None = None


def evaluate_mechanical_scope(
    packet: PlanScopePacket, *, current_base_sha: str, changes: Sequence[CanonicalChange]
) -> tuple[MechanicalScopeViolation, ...]:
    violations: list[MechanicalScopeViolation] = []
    if current_base_sha != packet.base_sha:
        violations.append(MechanicalScopeViolation(kind="base_sha_mismatch"))
    if len(changes) > packet.max_changed_files:
        violations.append(MechanicalScopeViolation(kind="changed_file_budget_exceeded"))
    total_lines = 0
    has_non_text_change = False
    for change in changes:
        paths = (
            (change.previous_path, change.path)
            if change.operation is GitOperation.RENAME
            else (change.path,)
        )
        for path in paths:
            assert path is not None
            if not any(
                fnmatch.fnmatchcase(path, pattern) for pattern in packet.allowed_path_patterns
            ):
                violations.append(
                    MechanicalScopeViolation(kind="path_outside_allowed_patterns", path=path)
                )
            permitted = {
                operation
                for rule in packet.allowed_operations
                if fnmatch.fnmatchcase(path, rule.pattern)
                for operation in rule.operations
            }
            if change.operation not in permitted:
                violations.append(MechanicalScopeViolation(kind="operation_not_allowed", path=path))
        if change.changed_lines is None:
            has_non_text_change = True
        else:
            total_lines += change.changed_lines
    if has_non_text_change or total_lines > packet.max_changed_lines:
        violations.append(MechanicalScopeViolation(kind="changed_line_budget_exceeded"))
    return tuple(violations)


class GateSnapshot(_ClosedModel):
    pull_request_number: int = Field(gt=0)
    base_sha: str = Field(min_length=1)
    plan_head_sha: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)


class ProvenanceReceipt(_ClosedModel):
    repository: str = Field(min_length=1)
    signer_path: str = Field(min_length=1)
    github_workflow_sha: str = Field(min_length=1)
    source_repository_digest: str = Field(min_length=1)
    workflow_run_id: int = Field(gt=0)
    verified: bool


class ArtifactReceipt(_ClosedModel):
    artifact_id: int = Field(gt=0)
    workflow_run_id: int = Field(gt=0)
    expired: bool = False
    record_bytes: bytes = Field(min_length=1)
    provenance: ProvenanceReceipt


class GateCandidate(_ClosedModel):
    artifact: ArtifactReceipt
    record: ReviewRecord

    @model_validator(mode="after")
    def _bind_execution_identities(self) -> "GateCandidate":
        if self.artifact.expired:
            raise ValueError("artifact is expired")
        if not self.artifact.provenance.verified:
            raise ValueError("attestation is not verified")
        if self.artifact.workflow_run_id != self.record.workflow_run_id:
            raise ValueError("artifact run does not match record run")
        if self.artifact.provenance.workflow_run_id != self.record.workflow_run_id:
            raise ValueError("attestation run does not match record run")
        if canonical_json(self.record) != self.artifact.record_bytes:
            raise ValueError("artifact bytes are not canonical record bytes")
        return self


def select_gate_evidence(
    candidates: Sequence[GateCandidate],
    *,
    snapshot: GateSnapshot,
    repository: str,
    signer_paths: Mapping[ReviewPhase, str],
    phase: ReviewPhase,
) -> ReviewRecord:
    """Return exactly one completed, provenance-bound record or fail closed."""
    matching: list[GateCandidate] = []
    for candidate in candidates:
        record = candidate.record
        provenance = candidate.artifact.provenance
        if record.phase is not phase:
            continue
        if phase is ReviewPhase.PLAN:
            if not isinstance(record.subject, PlanReviewSubject) or (
                record.subject.base_sha,
                record.subject.plan_head_sha,
            ) != (snapshot.base_sha, snapshot.plan_head_sha):
                continue
        elif not isinstance(record.subject, DiffReviewSubject) or (
            record.subject.base_sha,
            record.subject.plan_head_sha,
            record.subject.head_sha,
        ) != (snapshot.base_sha, snapshot.plan_head_sha, snapshot.head_sha):
            continue
        if provenance.repository != repository or provenance.signer_path != signer_paths[phase]:
            raise ValueError("unexpected signer provenance")
        if (
            provenance.github_workflow_sha != snapshot.base_sha
            or provenance.source_repository_digest != snapshot.base_sha
        ):
            raise ValueError("stale signer provenance")
        matching.append(candidate)
    if not matching:
        raise ValueError("missing matching review evidence")
    ordered = sorted(
        matching,
        key=lambda item: (
            item.record.emitted_at,
            item.record.workflow_run_id,
            item.record.record_id,
        ),
        reverse=True,
    )
    winner = ordered[0]
    winner_tuple = (
        winner.record.emitted_at,
        winner.record.workflow_run_id,
        winner.record.record_id,
    )
    if any(
        (item.record.emitted_at, item.record.workflow_run_id, item.record.record_id) == winner_tuple
        and item.artifact.record_bytes != winner.artifact.record_bytes
        for item in ordered[1:]
    ):
        raise ValueError("ambiguous review evidence selection")
    if winner.record.status is not ReviewStatus.COMPLETED:
        raise ValueError("review evidence is not completed")
    return winner.record


def gate_accepts(plan: ReviewRecord, diff: ReviewRecord) -> bool:
    """Findings need no resolution protocol to reject a blocking review."""
    return not any(
        finding.severity is Severity.BLOCKING
        for record in (plan, diff)
        for finding in record.findings
    )
