import runpy
from pathlib import Path


def test_v3_producers_use_trusted_base_and_canonical_packet_builder():
    root = Path(__file__).parents[1]
    plan = (root / ".github/workflows/review-plan.yml").read_text(encoding="utf-8")
    diff = (root / ".github/workflows/review-diff.yml").read_text(encoding="utf-8")
    for workflow in (plan, diff):
        assert "pull_request_target" in workflow
        assert "ref: ${{ github.event.pull_request.base.sha }}" in workflow
        assert "fetch-depth: 0" in workflow
        assert "persist-credentials: false" in workflow
        assert "review-evidence@2" not in workflow
    assert "labeled" not in plan
    assert "review_packet.py" in diff
    assert "--numstat" not in diff
    assert "--find-renames" not in diff


def test_consumer_is_pr_exclusive_and_never_checks_out_pr_head():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/review-evidence-gate.yml").read_text(encoding="utf-8")
    gate = (root / ".github/scripts/review_gate.py").read_text(encoding="utf-8")
    blocking_exit = runpy.run_path(root / ".github/scripts/review_gate.py")["BLOCKING_FINDING_EXIT"]
    assert "workflow_run" in workflow
    assert (
        "review-evidence-gate-${{ github.event.workflow_run.pull_requests[0].number }}" in workflow
    )
    assert "cancel-in-progress: true" in workflow
    assert "ref: ${{ steps.snapshot.outputs.base_sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "!github.event.workflow_run.pull_requests[0].head.repo.fork" in workflow
    assert 'git fetch --no-tags origin "$HEAD_SHA"' in workflow
    assert f'if test "$gate_status" = {blocking_exit}; then exit {blocking_exit}; fi' in workflow
    assert "echo superseded >&2; exit 1" in workflow
    assert "sleep 30" in workflow and "seq 1 30" in workflow
    assert "review-record.json" in gate
    assert 'print("blocking review finding", file=sys.stderr)' in gate
    assert "head_sha={head_sha}&per_page=100" in gate


def test_consumer_trigger_names_match_v3_producers():
    root = Path(__file__).parents[1]
    plan = (root / ".github/workflows/review-plan.yml").read_text(encoding="utf-8")
    diff = (root / ".github/workflows/review-diff.yml").read_text(encoding="utf-8")
    gate = (root / ".github/workflows/review-evidence-gate.yml").read_text(encoding="utf-8")
    assert "name: Attested plan review v3" in plan
    assert "name: Attested diff review v3" in diff
    assert 'workflows: ["Attested plan review v3", "Attested diff review v3"]' in gate


def test_provider_credentials_are_headers_and_requests_are_bounded():
    root = Path(__file__).parents[1]
    runner = (root / ".github/scripts/review_runner.py").read_text(encoding="utf-8")
    gate = (root / ".github/scripts/review_gate.py").read_text(encoding="utf-8")
    assert '"x-goog-api-key": os.environ["GOOGLE_API_KEY"]' in runner
    assert "?key=" not in runner
    assert "for attempt in range(3)" in runner
    assert "error.code == 429 or 500 <= error.code < 600" in runner
    assert "timeout=GH_TIMEOUT_SECONDS" in gate


def test_packet_blob_batch_has_a_timeout_and_cleans_up():
    root = Path(__file__).parents[1]
    packet = (root / ".github/scripts/review_packet.py").read_text(encoding="utf-8")
    assert "GIT_TIMEOUT_SECONDS = 60" in packet
    assert "process.communicate(request, timeout=GIT_TIMEOUT_SECONDS)" in packet
    assert "except subprocess.TimeoutExpired:" in packet
    assert "process.terminate()" in packet and "process.kill()" in packet
