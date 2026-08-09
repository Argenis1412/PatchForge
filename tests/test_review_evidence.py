from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orchestrator.review_evidence import (
    AllowedOperationRule,
    ChangedPath,
    CommitTransition,
    DiffReviewSubject,
    GitOperation,
    ModelTier,
    PlanReviewSubject,
    PlanScopePacket,
    ReviewPhase,
    ReviewRecord,
    ReviewStatus,
    UnavailableReason,
    evaluate_mechanical_scope,
    validate_linear_admission,
)


def _packet() -> PlanScopePacket:
    return PlanScopePacket(
        base_sha="base",
        plan_head_sha="plan",
        allowed_path_patterns=("src/**",),
        allowed_operations={"src/**": (GitOperation.ADD, GitOperation.MODIFY)},
        max_changed_files=1,
        max_changed_lines=10,
    )


def test_unavailable_record_requires_reason_and_never_findings():
    subject = PlanReviewSubject(
        base_sha="base", plan_head_sha="plan", packet_digest=_packet().digest
    )
    with pytest.raises(ValidationError, match="require unavailable_reason"):
        ReviewRecord(
            record_id="r",
            phase=ReviewPhase.PLAN,
            status=ReviewStatus.UNAVAILABLE,
            workflow_name="review-plan.yml",
            workflow_run_id=1,
            emitted_at=datetime.now(UTC),
            model_tier=ModelTier.ECONOMY,
            subject=subject,
        )
    record = ReviewRecord(
        record_id="r",
        phase=ReviewPhase.PLAN,
        status=ReviewStatus.UNAVAILABLE,
        workflow_name="review-plan.yml",
        workflow_run_id=1,
        emitted_at=datetime.now(UTC),
        model_tier=ModelTier.ECONOMY,
        subject=subject,
        unavailable_reason=UnavailableReason.TIMEOUT,
    )
    assert record.digest.startswith("sha256:")


def test_record_has_no_self_referential_attestation_reference():
    fields = ReviewRecord.model_fields
    assert "attestation_reference" not in fields


def test_mechanical_scope_checks_rename_both_sides_and_binary_budget():
    violations = evaluate_mechanical_scope(
        _packet(),
        current_base_sha="other",
        changes=(
            ChangedPath(
                operation=GitOperation.RENAME,
                previous_path="src/a.py",
                path="outside.py",
                changed_lines=None,
                is_binary=True,
            ),
        ),
    )
    assert {item.kind for item in violations} == {
        "base_sha_mismatch",
        "path_outside_allowed_patterns",
        "operation_not_allowed",
        "changed_line_budget_exceeded",
    }


def test_linear_admission_rejects_hidden_history_and_merges():
    plan = CommitTransition(
        sha="plan", parents=("intermediate",), changed_paths=(".patchforge/review-plan.json",)
    )
    errors = validate_linear_admission(
        base_sha="base",
        plan_head_sha="plan",
        plan_commit=plan,
        post_plan=(CommitTransition(sha="head", parents=("plan", "main")),),
    )
    assert "only parent" in errors[0]
    assert "linear chain" in errors[1]


def test_linear_admission_accepts_one_plan_transition_and_linear_implementation():
    errors = validate_linear_admission(
        base_sha="base",
        plan_head_sha="plan",
        plan_commit=CommitTransition(
            sha="plan", parents=("base",), changed_paths=(".patchforge/review-plan.json",)
        ),
        post_plan=(
            CommitTransition(sha="code-1", parents=("plan",)),
            CommitTransition(sha="head", parents=("code-1",)),
        ),
    )
    assert errors == ()


def test_allowed_operations_are_deeply_immutable():
    packet = _packet()
    assert packet.allowed_operations == (
        AllowedOperationRule(pattern="src/**", operations=(GitOperation.ADD, GitOperation.MODIFY)),
    )
    with pytest.raises(ValidationError):
        packet.allowed_operations[0].operations += (GitOperation.DELETE,)


def test_subjects_cannot_cross_phase():
    with pytest.raises(ValidationError):
        ReviewRecord(
            record_id="r",
            phase=ReviewPhase.PLAN,
            status=ReviewStatus.COMPLETED,
            workflow_name="review-plan.yml",
            workflow_run_id=1,
            emitted_at=datetime.now(UTC),
            model_tier=ModelTier.ECONOMY,
            subject=DiffReviewSubject(
                base_sha="base",
                plan_head_sha="plan",
                head_sha="head",
                diff_digest="sha256:" + "0" * 64,
            ),
        )
