from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orchestrator.review_evidence import (
    AdmissionReason,
    AdmissionSubject,
    ArtifactReceipt,
    CanonicalChange,
    CanonicalChangeSet,
    DiffReviewPacket,
    GateCandidate,
    GateSnapshot,
    GitOperation,
    ModelTier,
    PlanReviewSubject,
    PlanScopeDeclaration,
    PlanScopePacket,
    ProvenanceReceipt,
    ReviewPhase,
    ReviewRecord,
    ReviewStatus,
    TreeEntry,
    UnavailableReason,
    build_canonical_change_set,
    canonical_json,
    parse_review_record,
    select_gate_evidence,
)


def entry(path: str, oid: str, *, mode: str = "100644", kind: str = "blob") -> TreeEntry:
    return TreeEntry(path=path, object_id=oid, mode=mode, object_type=kind)  # type: ignore[arg-type]


def test_version_dispatch_rejects_historical_and_malformed_records_first():
    for value in (
        {"schema_version": "review-evidence@2", "status": "completed"},
        {"schema_version": "unknown"},
        b"[]",
    ):
        with pytest.raises(ValueError):
            parse_review_record(value)


def test_record_binds_its_run_identity_and_status_shape():
    subject = AdmissionSubject(phase=ReviewPhase.PLAN, base_sha="base", head_sha="head")
    args = {
        "phase": ReviewPhase.PLAN,
        "status": ReviewStatus.ADMISSION_REJECTED,
        "workflow_name": "descriptive",
        "workflow_run_id": 9,
        "emitted_at": datetime.now(UTC),
        "subject": subject,
        "admission_reason": AdmissionReason.PLAN_TRANSITION_INVALID,
    }
    assert ReviewRecord(record_id="9:plan_review", **args).schema_version == "review-evidence@3"
    with pytest.raises(ValidationError, match="record_id"):
        ReviewRecord(record_id="other", **args)
    with pytest.raises(ValidationError, match="invalid payload"):
        ReviewRecord(
            record_id="9:plan_review",
            **{
                **args,
                "status": ReviewStatus.UNAVAILABLE,
                "model_tier": ModelTier.ECONOMY,
                "unavailable_reason": UnavailableReason.TIMEOUT,
            },
        )
    with pytest.raises(ValidationError, match="phase"):
        ReviewRecord(
            record_id="9:plan_review",
            **{
                **args,
                "subject": AdmissionSubject(
                    phase=ReviewPhase.DIFF, base_sha="base", head_sha="head"
                ),
            },
        )


def test_rename_is_only_a_unique_same_blob_same_mode_pair():
    changes, texts = build_canonical_change_set(
        [entry("old.py", "a")], [entry("new.py", "a")], {"a": b"one\n"}
    )
    assert [
        (change.operation, change.previous_path, change.path) for change in changes.changes
    ] == [(GitOperation.RENAME, "old.py", "new.py")]
    assert texts[0].old_text == "one\n" and texts[0].new_text == "one\n"
    changes, _ = build_canonical_change_set(
        [entry("a.py", "x"), entry("b.py", "x")], [entry("c.py", "x")], {"x": b"x"}
    )
    assert [change.operation for change in changes.changes] == [
        GitOperation.DELETE,
        GitOperation.DELETE,
        GitOperation.ADD,
    ]


def test_edited_move_and_copy_are_not_renames():
    changes, _ = build_canonical_change_set(
        [entry("old.py", "old")], [entry("new.py", "new")], {"old": b"x", "new": b"x2"}
    )
    assert [change.operation for change in changes.changes] == [
        GitOperation.ADD,
        GitOperation.DELETE,
    ]
    changes, _ = build_canonical_change_set(
        [entry("source.py", "old")],
        [entry("source.py", "old"), entry("copy.py", "old")],
        {"old": b"x"},
    )
    assert [change.operation for change in changes.changes] == [GitOperation.ADD]


@pytest.mark.parametrize(
    ("old", "new", "blobs", "reason"),
    [
        ([entry("a", "a")], [entry("a", "b")], {"a": b"a", "b": b"\0"}, "binary"),
        ([entry("sub", "a", kind="commit")], [entry("sub", "b", kind="commit")], {}, "submodule"),
        ([entry("a", "a")], [entry("a", "b")], {"a": b"a", "b": b"\xff"}, "invalid_utf8"),
    ],
)
def test_non_text_changes_have_no_line_budget(old, new, blobs, reason):
    changes, texts = build_canonical_change_set(old, new, blobs)
    assert changes.changes[0].non_text_reason == reason
    assert changes.changes[0].changed_lines is None
    assert texts == ()


def test_mode_change_is_modify_and_physical_lines_are_counted():
    changes, texts = build_canonical_change_set(
        [entry("a", "x", mode="100644")],
        [entry("a", "y", mode="100755")],
        {"x": b"a\n", "y": b"b\n\nlast"},
    )
    assert changes.changes[0].operation is GitOperation.MODIFY
    assert changes.changes[0].changed_lines == 4
    assert texts[0].old_text == "a\n" and texts[0].new_text == "b\n\nlast"


def test_paths_use_normative_utf8_byte_order_and_fail_closed():
    changes, _ = build_canonical_change_set(
        [], [entry("z", "z"), entry("á", "a")], {"z": b"", "a": b""}
    )
    assert [change.path for change in changes.changes] == ["z", "á"]
    with pytest.raises(ValidationError, match="valid string"):
        entry("bad\ud800", "a")


def test_packet_digest_binds_change_set_and_exact_text_sides():
    changes, texts = build_canonical_change_set([], [entry("empty", "e")], {"e": b""})
    packet = DiffReviewPacket(canonical_change_set=changes, text_deltas=texts)
    assert packet.text_deltas[0].old_text is None
    assert packet.text_deltas[0].new_text == ""
    assert packet.digest.startswith("sha256:")
    assert CanonicalChangeSet.model_validate(changes.model_dump()).digest == changes.digest
    assert canonical_json({"packet": packet})


def test_canonical_change_rejects_operation_entry_mismatches():
    old_entry = entry("old", "old")
    new_entry = entry("new", "new")
    with pytest.raises(ValidationError, match="add cannot contain old_entry"):
        CanonicalChange(
            operation=GitOperation.ADD,
            path="new",
            old_entry=old_entry,
            new_entry=new_entry,
            changed_lines=2,
        )
    with pytest.raises(ValidationError, match="delete cannot contain new_entry"):
        CanonicalChange(
            operation=GitOperation.DELETE,
            path="old",
            old_entry=old_entry,
            new_entry=new_entry,
            changed_lines=2,
        )


def test_scope_packet_stays_declaration_bound():
    declaration = PlanScopeDeclaration(
        allowed_path_patterns=("src/**",),
        allowed_operations={"src/**": (GitOperation.ADD,)},
        max_changed_files=1,
        max_changed_lines=2,
    )
    packet = PlanScopePacket.from_declaration(declaration, base_sha="base", plan_head_sha="plan")
    assert packet.base_sha == "base" and "plan_head_sha" not in declaration.model_dump()
    assert packet.allowed_path_patterns == declaration.allowed_path_patterns
    assert packet.allowed_operations == declaration.allowed_operations
    assert packet.max_changed_files == declaration.max_changed_files
    assert packet.max_changed_lines == declaration.max_changed_lines


def test_gate_requires_artifact_record_and_signed_run_identity():
    snapshot = GateSnapshot(
        pull_request_number=330, base_sha="base", plan_head_sha="plan", head_sha="head"
    )
    subject = PlanReviewSubject(
        base_sha="base", plan_head_sha="plan", packet_digest="sha256:" + "0" * 64
    )
    record = ReviewRecord(
        record_id="7:plan_review",
        phase=ReviewPhase.PLAN,
        status=ReviewStatus.COMPLETED,
        workflow_name="descriptive",
        workflow_run_id=7,
        emitted_at=datetime.now(UTC),
        model_tier=ModelTier.ECONOMY,
        subject=subject,
    )
    receipt = ArtifactReceipt(
        artifact_id=99,
        workflow_run_id=7,
        record_bytes=record.model_dump_json(exclude_none=False).encode(),
        provenance=ProvenanceReceipt(
            repository="owner/repo",
            signer_path=".github/workflows/review-plan.yml",
            github_workflow_sha="base",
            source_repository_digest="base",
            workflow_run_id=7,
            verified=True,
        ),
    )
    with pytest.raises(ValidationError, match="canonical"):
        GateCandidate(artifact=receipt, record=record)
    receipt = receipt.model_copy(update={"record_bytes": canonical_json(record)})
    selected = select_gate_evidence(
        [GateCandidate(artifact=receipt, record=record)],
        snapshot=snapshot,
        repository="owner/repo",
        signer_paths={
            ReviewPhase.PLAN: ".github/workflows/review-plan.yml",
            ReviewPhase.DIFF: ".github/workflows/review-diff.yml",
        },
        phase=ReviewPhase.PLAN,
    )
    assert selected.record_id == "7:plan_review"


def test_gate_rejects_non_completed_evidence():
    snapshot = GateSnapshot(
        pull_request_number=330, base_sha="base", plan_head_sha="plan", head_sha="head"
    )
    subject = PlanReviewSubject(
        base_sha="base", plan_head_sha="plan", packet_digest="sha256:" + "0" * 64
    )
    record = ReviewRecord(
        record_id="8:plan_review",
        phase=ReviewPhase.PLAN,
        status=ReviewStatus.UNAVAILABLE,
        workflow_name="descriptive",
        workflow_run_id=8,
        emitted_at=datetime.now(UTC),
        model_tier=ModelTier.ECONOMY,
        subject=subject,
        unavailable_reason=UnavailableReason.TIMEOUT,
    )
    receipt = ArtifactReceipt(
        artifact_id=100,
        workflow_run_id=8,
        record_bytes=canonical_json(record),
        provenance=ProvenanceReceipt(
            repository="owner/repo",
            signer_path=".github/workflows/review-plan.yml",
            github_workflow_sha="base",
            source_repository_digest="base",
            workflow_run_id=8,
            verified=True,
        ),
    )
    with pytest.raises(ValueError, match="not completed"):
        select_gate_evidence(
            [GateCandidate(artifact=receipt, record=record)],
            snapshot=snapshot,
            repository="owner/repo",
            signer_paths={
                ReviewPhase.PLAN: ".github/workflows/review-plan.yml",
                ReviewPhase.DIFF: ".github/workflows/review-diff.yml",
            },
            phase=ReviewPhase.PLAN,
        )


def _completed_plan_candidate(
    *, artifact_id: int, plan_head_sha: str, emitted_at: datetime, workflow_name: str
) -> GateCandidate:
    record = ReviewRecord(
        record_id="10:plan_review",
        phase=ReviewPhase.PLAN,
        status=ReviewStatus.COMPLETED,
        workflow_name=workflow_name,
        workflow_run_id=10,
        emitted_at=emitted_at,
        model_tier=ModelTier.ECONOMY,
        subject=PlanReviewSubject(
            base_sha="base",
            plan_head_sha=plan_head_sha,
            packet_digest="sha256:" + "1" * 64,
        ),
    )
    return GateCandidate(
        artifact=ArtifactReceipt(
            artifact_id=artifact_id,
            workflow_run_id=10,
            record_bytes=canonical_json(record),
            provenance=ProvenanceReceipt(
                repository="owner/repo",
                signer_path=".github/workflows/review-plan.yml",
                github_workflow_sha="base",
                source_repository_digest="base",
                workflow_run_id=10,
                verified=True,
            ),
        ),
        record=record,
    )


def test_gate_skips_superseded_subject_when_current_candidate_exists():
    emitted_at = datetime.now(UTC)
    stale = _completed_plan_candidate(
        artifact_id=101,
        plan_head_sha="old-plan",
        emitted_at=emitted_at,
        workflow_name="stale",
    )
    current = _completed_plan_candidate(
        artifact_id=102,
        plan_head_sha="plan",
        emitted_at=emitted_at,
        workflow_name="current",
    )
    selected = select_gate_evidence(
        [stale, current],
        snapshot=GateSnapshot(
            pull_request_number=330,
            base_sha="base",
            plan_head_sha="plan",
            head_sha="head",
        ),
        repository="owner/repo",
        signer_paths={
            ReviewPhase.PLAN: ".github/workflows/review-plan.yml",
            ReviewPhase.DIFF: ".github/workflows/review-diff.yml",
        },
        phase=ReviewPhase.PLAN,
    )
    assert selected.workflow_name == "current"


def test_gate_selects_newer_completed_evidence_over_older_unavailable_retry():
    emitted_at = datetime.now(UTC)
    completed = _completed_plan_candidate(
        artifact_id=106,
        plan_head_sha="plan",
        emitted_at=emitted_at,
        workflow_name="completed",
    )
    unavailable_record = ReviewRecord.model_validate(
        {
            **completed.record.model_dump(),
            "status": ReviewStatus.UNAVAILABLE,
            "findings": (),
            "unavailable_reason": UnavailableReason.TIMEOUT,
            "emitted_at": datetime.min.replace(tzinfo=UTC),
        }
    )
    unavailable = GateCandidate(
        artifact=completed.artifact.model_copy(
            update={
                "artifact_id": 107,
                "record_bytes": canonical_json(unavailable_record),
            }
        ),
        record=unavailable_record,
    )
    selected = select_gate_evidence(
        [unavailable, completed],
        snapshot=GateSnapshot(
            pull_request_number=330,
            base_sha="base",
            plan_head_sha="plan",
            head_sha="head",
        ),
        repository="owner/repo",
        signer_paths={
            ReviewPhase.PLAN: ".github/workflows/review-plan.yml",
            ReviewPhase.DIFF: ".github/workflows/review-diff.yml",
        },
        phase=ReviewPhase.PLAN,
    )
    assert selected.status is ReviewStatus.COMPLETED
    newer_unavailable_record = ReviewRecord.model_validate(
        {
            **unavailable_record.model_dump(),
            "emitted_at": datetime.max.replace(tzinfo=UTC),
        }
    )
    newer_unavailable = GateCandidate(
        artifact=unavailable.artifact.model_copy(
            update={"record_bytes": canonical_json(newer_unavailable_record)}
        ),
        record=newer_unavailable_record,
    )
    with pytest.raises(ValueError, match="not completed"):
        select_gate_evidence(
            [completed, newer_unavailable],
            snapshot=GateSnapshot(
                pull_request_number=330,
                base_sha="base",
                plan_head_sha="plan",
                head_sha="head",
            ),
            repository="owner/repo",
            signer_paths={
                ReviewPhase.PLAN: ".github/workflows/review-plan.yml",
                ReviewPhase.DIFF: ".github/workflows/review-diff.yml",
            },
            phase=ReviewPhase.PLAN,
        )


def test_gate_rejects_equal_selection_tuple_with_different_bytes():
    emitted_at = datetime.now(UTC)
    first = _completed_plan_candidate(
        artifact_id=103,
        plan_head_sha="plan",
        emitted_at=emitted_at,
        workflow_name="first",
    )
    second = _completed_plan_candidate(
        artifact_id=104,
        plan_head_sha="plan",
        emitted_at=emitted_at,
        workflow_name="second",
    )
    with pytest.raises(ValueError, match="ambiguous"):
        select_gate_evidence(
            [first, second],
            snapshot=GateSnapshot(
                pull_request_number=330,
                base_sha="base",
                plan_head_sha="plan",
                head_sha="head",
            ),
            repository="owner/repo",
            signer_paths={
                ReviewPhase.PLAN: ".github/workflows/review-plan.yml",
                ReviewPhase.DIFF: ".github/workflows/review-diff.yml",
            },
            phase=ReviewPhase.PLAN,
        )


def test_gate_rejects_stale_signer_provenance():
    candidate = _completed_plan_candidate(
        artifact_id=105,
        plan_head_sha="plan",
        emitted_at=datetime.now(UTC),
        workflow_name="current",
    )
    stale_receipt = candidate.artifact.model_copy(
        update={
            "provenance": candidate.artifact.provenance.model_copy(
                update={"github_workflow_sha": "old"}
            )
        }
    )
    with pytest.raises(ValueError, match="stale signer"):
        select_gate_evidence(
            [GateCandidate(artifact=stale_receipt, record=candidate.record)],
            snapshot=GateSnapshot(
                pull_request_number=330,
                base_sha="base",
                plan_head_sha="plan",
                head_sha="head",
            ),
            repository="owner/repo",
            signer_paths={
                ReviewPhase.PLAN: ".github/workflows/review-plan.yml",
                ReviewPhase.DIFF: ".github/workflows/review-diff.yml",
            },
            phase=ReviewPhase.PLAN,
        )
