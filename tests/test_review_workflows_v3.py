from pathlib import Path


def test_v3_producers_use_trusted_base_and_canonical_packet_builder():
    root = Path(__file__).parents[1]
    plan = (root / ".github/workflows/review-plan.yml").read_text(encoding="utf-8")
    diff = (root / ".github/workflows/review-diff.yml").read_text(encoding="utf-8")
    for workflow in (plan, diff):
        assert "pull_request_target" in workflow
        assert "ref: ${{ github.event.pull_request.base.sha }}" in workflow
        assert "fetch-depth: 0" in workflow
        assert "review-evidence@2" not in workflow
    assert "review_packet.py" in diff
    assert "--numstat" not in diff
    assert "--find-renames" not in diff


def test_consumer_is_pr_exclusive_and_never_checks_out_pr_head():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/review-evidence-gate.yml").read_text(encoding="utf-8")
    gate = (root / ".github/scripts/review_gate.py").read_text(encoding="utf-8")
    assert "workflow_run" in workflow
    assert (
        "review-evidence-gate-${{ github.event.workflow_run.pull_requests[0].number }}" in workflow
    )
    assert "cancel-in-progress: true" in workflow
    assert "ref: ${{ steps.snapshot.outputs.base_sha }}" in workflow
    assert "sleep 30" in workflow and "seq 1 30" in workflow
    assert "actions/checkout" not in gate
    assert "review-record.json" in gate
