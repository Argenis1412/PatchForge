from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orchestrator.review_evidence import (
    AdmissionReason,
    AdmissionSubject,
    ArtifactReceipt,
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


def test_paths_are_byte_sorted_and_unrepresentable_paths_fail_closed():
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


def test_scope_packet_stays_declaration_bound():
    declaration = PlanScopeDeclaration(
        allowed_path_patterns=("src/**",),
        allowed_operations={"src/**": (GitOperation.ADD,)},
        max_changed_files=1,
        max_changed_lines=2,
    )
    packet = PlanScopePacket.from_declaration(declaration, base_sha="base", plan_head_sha="plan")
    assert packet.base_sha == "base" and "plan_head_sha" not in declaration.model_dump()


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


def test_gate_rejects_non_completed_stale_and_ambiguous_evidence():
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
    stale = receipt.model_copy(
        update={"provenance": receipt.provenance.model_copy(update={"github_workflow_sha": "old"})}
    )
    with pytest.raises(ValueError, match="stale signer"):
        select_gate_evidence(
            [GateCandidate(artifact=stale, record=record)],
            snapshot=snapshot,
            repository="owner/repo",
            signer_paths={
                ReviewPhase.PLAN: ".github/workflows/review-plan.yml",
                ReviewPhase.DIFF: ".github/workflows/review-diff.yml",
            },
            phase=ReviewPhase.PLAN,
        )
