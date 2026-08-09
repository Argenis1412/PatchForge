import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.review_evidence import (
    AdmissionReason,
    AllowedOperationRule,
    ChangedPath,
    CommitTransition,
    DeclarationProvenance,
    DeclarationProvenanceKind,
    DiffAdmissionSubject,
    DiffReviewSubject,
    GitOperation,
    ModelTier,
    PlanAdmissionSubject,
    PlanReviewSubject,
    PlanScopeDeclaration,
    PlanScopePacket,
    ReviewPhase,
    ReviewRecord,
    ReviewStatus,
    UnavailableReason,
    evaluate_mechanical_scope,
    parse_review_record,
    validate_linear_admission,
)


def _declaration() -> PlanScopeDeclaration:
    return PlanScopeDeclaration(
        allowed_path_patterns=("src/**",),
        allowed_operations={"src/**": (GitOperation.ADD, GitOperation.MODIFY)},
        max_changed_files=1,
        max_changed_lines=10,
    )


def _packet() -> PlanScopePacket:
    return PlanScopePacket.from_declaration(_declaration(), base_sha="base", plan_head_sha="plan")


def _record_args() -> dict[str, object]:
    return {
        "record_id": "r",
        "phase": ReviewPhase.PLAN,
        "workflow_name": "review-plan.yml",
        "workflow_run_id": 1,
        "emitted_at": datetime.now(UTC),
    }


def _runner_module():
    runner_path = Path(__file__).parents[1] / ".github" / "scripts" / "review_runner.py"
    spec = importlib.util.spec_from_file_location("review_runner_test", runner_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_execution_records_require_admitted_subject_and_tier():
    subject = PlanReviewSubject(
        base_sha="base", plan_head_sha="plan", packet_digest=_packet().digest
    )
    record = ReviewRecord(
        status=ReviewStatus.UNAVAILABLE,
        model_tier=ModelTier.ECONOMY,
        subject=subject,
        unavailable_reason=UnavailableReason.TIMEOUT,
        **_record_args(),
    )
    assert record.schema_version == "review-evidence@2"
    with pytest.raises(ValidationError, match="require model_tier"):
        ReviewRecord(status=ReviewStatus.COMPLETED, subject=subject, **_record_args())


@pytest.mark.parametrize(
    ("kind", "digest"),
    [
        (DeclarationProvenanceKind.VALIDATED, "sha256:" + "1" * 64),
        (DeclarationProvenanceKind.RAW, "sha256:" + "2" * 64),
        (DeclarationProvenanceKind.ABSENT, None),
        (DeclarationProvenanceKind.UNREADABLE, None),
    ],
)
def test_declaration_provenance_has_exactly_the_permitted_digest_shape(kind, digest):
    provenance = DeclarationProvenance(kind=kind, digest=digest)
    assert provenance.digest == digest


def test_declaration_provenance_rejects_digest_mismatch():
    with pytest.raises(ValidationError, match="must match its kind"):
        DeclarationProvenance(kind=DeclarationProvenanceKind.ABSENT, digest="sha256:" + "0" * 64)


def test_admission_record_has_no_tier_or_findings_and_binds_candidate_subject():
    provenance = DeclarationProvenance(kind=DeclarationProvenanceKind.ABSENT)
    subject = PlanAdmissionSubject(
        base_sha="base", head_sha="candidate", declaration_provenance=provenance
    )
    record = ReviewRecord(
        status=ReviewStatus.ADMISSION_REJECTED,
        admission_reason=AdmissionReason.PLAN_TRANSITION_INVALID,
        subject=subject,
        **_record_args(),
    )
    assert record.model_tier is None
    with pytest.raises(ValidationError, match="cannot contain tier"):
        ReviewRecord(
            status=ReviewStatus.ADMISSION_REJECTED,
            admission_reason=AdmissionReason.PLAN_TRANSITION_INVALID,
            model_tier=ModelTier.ECONOMY,
            subject=subject,
            **_record_args(),
        )


def test_declaration_rejection_reason_must_match_provenance():
    subject = PlanAdmissionSubject(
        base_sha="base",
        head_sha="candidate",
        declaration_provenance=DeclarationProvenance(
            kind=DeclarationProvenanceKind.RAW, digest="sha256:" + "3" * 64
        ),
    )
    with pytest.raises(ValidationError, match="must match declaration provenance"):
        ReviewRecord(
            status=ReviewStatus.ADMISSION_REJECTED,
            admission_reason=AdmissionReason.DECLARATION_ABSENT,
            subject=subject,
            **_record_args(),
        )


def test_runner_materializes_harness_rejection_without_model_tier(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_WORKFLOW_REF", "owner/repo/.github/workflows/review-plan.yml@main")
    runner = _runner_module()
    record = runner.materialize_admission_record(
        phase=ReviewPhase.PLAN,
        base_sha="base",
        head_sha="head",
        reason=AdmissionReason.DECLARATION_ABSENT,
        provenance=DeclarationProvenance(kind=DeclarationProvenanceKind.ABSENT),
    )
    assert record.status is ReviewStatus.ADMISSION_REJECTED
    assert record.model_tier is None
    assert record.findings == ()


def test_version_dispatch_rejects_v1_before_payload_interpretation():
    with pytest.raises(ValueError, match="unsupported review evidence schema version"):
        parse_review_record({"schema_version": "review-evidence@1", "status": "completed"})


@pytest.mark.parametrize("value", [b"[]", '"record"', "0", "null"])
def test_version_dispatch_rejects_non_object_json(value):
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_review_record(value)


def test_record_has_no_self_referential_attestation_reference():
    assert "attestation_reference" not in ReviewRecord.model_fields


def test_runner_detects_missing_credential_before_model_call(monkeypatch):
    runner = _runner_module()
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert not runner._credential_available(ModelTier.ECONOMY)


def test_runner_appends_github_output(tmp_path):
    runner = _runner_module()
    output = tmp_path / "github-output"
    output.write_text("existing=value\n", encoding="utf-8")
    record = ReviewRecord(
        status=ReviewStatus.COMPLETED,
        model_tier=ModelTier.ECONOMY,
        subject=PlanReviewSubject(
            base_sha="base", plan_head_sha="plan", packet_digest=_packet().digest
        ),
        **_record_args(),
    )
    runner._append_github_output(output, record, 330)
    assert output.read_text(encoding="utf-8").startswith("existing=value\nartifact_name=")


def test_review_workflows_keep_history_and_binary_packets_safe():
    root = Path(__file__).parents[1]
    plan_workflow = (root / ".github" / "workflows" / "review-plan.yml").read_text(encoding="utf-8")
    diff_workflow = (root / ".github" / "workflows" / "review-diff.yml").read_text(encoding="utf-8")
    assert "fetch-depth: 0" in plan_workflow
    assert "fetch-depth: 0" in diff_workflow
    assert 'decode("utf-8", errors="replace")' in diff_workflow
    assert "print binary ? 101 : sum + 0" in diff_workflow


def test_review_workflows_install_the_harness_dependency():
    root = Path(__file__).parents[1]
    for workflow_name in ("review-plan.yml", "review-diff.yml"):
        workflow = (root / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")
        assert "actions/setup-python@v5" in workflow
        assert 'python -m pip install "pydantic>=2.10.0"' in workflow


def test_subject_digest_is_stable_across_retry_metadata_and_artifact_name_is_canonical():
    subject = DiffReviewSubject(
        base_sha="base",
        plan_head_sha="plan",
        head_sha="head",
        diff_digest="sha256:" + "0" * 64,
    )
    first = ReviewRecord(
        phase=ReviewPhase.DIFF,
        status=ReviewStatus.COMPLETED,
        model_tier=ModelTier.ECONOMY,
        subject=subject,
        **{key: value for key, value in _record_args().items() if key != "phase"},
    )
    second = first.model_copy(update={"record_id": "retry", "workflow_run_id": 2})
    assert first.subject_digest == second.subject_digest
    assert first.digest != second.digest
    assert first.artifact_name(330) == (
        f"review-evidence-diff_review-330-sha256-{first.subject_digest.removeprefix('sha256:')}-1"
    )


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
    errors = validate_linear_admission(
        base_sha="base",
        plan_head_sha="plan",
        plan_commit=CommitTransition(
            sha="plan", parents=("intermediate",), changed_paths=(".patchforge/review-plan.json",)
        ),
        post_plan=(CommitTransition(sha="head", parents=("plan", "main")),),
    )
    assert "only parent" in errors[0]
    assert "linear chain" in errors[1]


def test_linear_admission_accepts_one_plan_transition_and_linear_implementation():
    assert (
        validate_linear_admission(
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
        == ()
    )


def test_candidate_subjects_cannot_be_used_as_completed_records():
    subject = DiffAdmissionSubject(
        base_sha="base",
        head_sha="head",
        declaration_provenance=DeclarationProvenance(kind=DeclarationProvenanceKind.UNREADABLE),
    )
    with pytest.raises(ValidationError, match="admitted subject"):
        ReviewRecord(
            phase=ReviewPhase.DIFF,
            status=ReviewStatus.COMPLETED,
            model_tier=ModelTier.ECONOMY,
            subject=subject,
            **{key: value for key, value in _record_args().items() if key != "phase"},
        )


def test_declaration_becomes_canonical_packet_only_after_trusted_shas_are_added():
    packet = _packet()
    assert packet.base_sha == "base"
    assert packet.plan_head_sha == "plan"
    assert "plan_head_sha" not in _declaration().model_dump()
    assert packet.allowed_operations == (
        AllowedOperationRule(pattern="src/**", operations=(GitOperation.ADD, GitOperation.MODIFY)),
    )
