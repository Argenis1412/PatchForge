"""Fail-closed advisory consumer for attested review-evidence@3.

The workflow supplies only GitHub API data.  This script never checks out the
pull request head; its working directory is the trusted base checkout.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from orchestrator.review_evidence import (
    ArtifactReceipt,
    GateCandidate,
    GateDecision,
    GateResult,
    GateSnapshot,
    ProvenanceReceipt,
    ReviewPhase,
    canonical_json,
    evaluate_gate_evidence,
    parse_review_record,
)

GH_TIMEOUT_SECONDS = 60
BLOCKING_PENDING_EXIT = 2
TRIAGE_REQUIRED_EXIT = 3
PENDING_DIFF_EXIT = 4


def _gh(*args: str) -> bytes:
    return subprocess.run(
        ["gh", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=GH_TIMEOUT_SECONDS,
    ).stdout


def _value(value: object, *path: str) -> object:
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"attestation is missing {'.'.join(path)}")
        value = value[key]
    return value


def _verified_provenance(
    record_path: Path,
    *,
    repository: str,
    signer_path: str,
    base_sha: str,
) -> ProvenanceReceipt:
    """Verify bytes first, then extract the signed statement fields required by ADR-0015."""
    raw = _gh(
        "attestation",
        "verify",
        str(record_path),
        "--repo",
        repository,
        "--signer-workflow",
        f"{repository}/{signer_path}",
        "--signer-digest",
        base_sha,
        "--source-digest",
        base_sha,
        "--format",
        "json",
    )
    verified = json.loads(raw)
    if not isinstance(verified, list) or len(verified) != 1:
        raise ValueError("artifact must have exactly one verified attestation")
    statement = _value(verified[0], "verificationResult", "statement")
    predicate = _value(statement, "predicate")
    workflow = _value(predicate, "buildDefinition", "externalParameters", "workflow")
    repository_uri = str(_value(workflow, "repository"))
    repository = repository_uri.removeprefix("https://github.com/").rstrip("/")
    certificate = _value(verified[0], "verificationResult", "signature", "certificate")
    invocation_id = str(_value(predicate, "runDetails", "metadata", "invocationId"))
    match = re.search(r"/runs/(\d+)(?:/|$)", invocation_id)
    if match is None:
        raise ValueError("attestation invocation does not contain a workflow run")
    run_id = int(match.group(1))
    return ProvenanceReceipt(
        repository=repository,
        signer_path=str(_value(workflow, "path")),
        github_workflow_sha=str(_value(certificate, "githubWorkflowSHA")),
        source_repository_digest=str(_value(certificate, "sourceRepositoryDigest")),
        workflow_run_id=run_id,
        verified=True,
    )


def _artifact_candidates(*, repository: str, snapshot: GateSnapshot) -> list[GateCandidate]:
    candidates: list[GateCandidate] = []
    runs: list[object] = []
    for workflow_name, head_sha in (
        ("review-plan.yml", snapshot.plan_head_sha),
        ("review-diff.yml", snapshot.head_sha),
    ):
        pages = json.loads(
            _gh(
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/actions/workflows/{workflow_name}/runs"
                f"?event=pull_request_target&head_sha={head_sha}&per_page=100",
            )
        )
        runs.extend(run for page in pages for run in page.get("workflow_runs", []))
    artifacts: list[object] = []
    for run in runs:
        if not isinstance(run, dict) or not any(
            pull.get("number") == snapshot.pull_request_number
            for pull in run.get("pull_requests", [])
        ):
            continue
        pages = json.loads(
            _gh(
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/actions/runs/{run['id']}/artifacts?per_page=100",
            )
        )
        artifacts.extend(artifact for page in pages for artifact in page.get("artifacts", []))
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if not str(artifact.get("name", "")).startswith("review-evidence-"):
            continue
        if artifact.get("expired"):
            continue
        artifact_id, run_id = int(artifact["id"]), int(artifact["workflow_run"]["id"])
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "artifact.zip"
            archive.write_bytes(
                _gh("api", f"repos/{repository}/actions/artifacts/{artifact_id}/zip")
            )
            with zipfile.ZipFile(archive) as bundle:
                names = [name for name in bundle.namelist() if not name.endswith("/")]
                if names != ["review-record.json"]:
                    raise ValueError("evidence artifact must contain exactly review-record.json")
                record_bytes = bundle.read("review-record.json")
            raw_record = json.loads(record_bytes)
            if isinstance(raw_record, dict) and raw_record.get("schema_version") in {
                "review-evidence@1",
                "review-evidence@2",
            }:
                continue
            record = parse_review_record(raw_record)
            record_path = Path(directory) / "review-record.json"
            record_path.write_bytes(record_bytes)
            provenance = _verified_provenance(
                record_path,
                repository=repository,
                signer_path=(
                    ".github/workflows/review-plan.yml"
                    if record.phase is ReviewPhase.PLAN
                    else ".github/workflows/review-diff.yml"
                ),
                base_sha=snapshot.base_sha,
            )
            candidates.append(
                GateCandidate(
                    artifact=ArtifactReceipt(
                        artifact_id=artifact_id,
                        workflow_run_id=run_id,
                        expired=False,
                        record_bytes=record_bytes,
                        provenance=provenance,
                    ),
                    record=record,
                )
            )
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--plan-head-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument(
        "--terminal-result",
        choices=["superseded", "evidence_incomplete"],
        help="Emit a trusted workflow-level terminal result without discovering evidence.",
    )
    args = parser.parse_args()
    snapshot = GateSnapshot(
        pull_request_number=args.pr_number,
        base_sha=args.base_sha,
        plan_head_sha=args.plan_head_sha,
        head_sha=args.head_sha,
    )
    if args.terminal_result:
        print(
            canonical_json(
                GateDecision(result=GateResult(args.terminal_result), snapshot=snapshot)
            ).decode("utf-8")
        )
        return
    candidates = _artifact_candidates(repository=args.repository, snapshot=snapshot)
    signers = {
        ReviewPhase.PLAN: ".github/workflows/review-plan.yml",
        ReviewPhase.DIFF: ".github/workflows/review-diff.yml",
    }
    decision = evaluate_gate_evidence(
        candidates,
        snapshot=snapshot,
        repository=args.repository,
        signer_paths=signers,
    )
    print(canonical_json(decision).decode("utf-8"))
    if decision.result is GateResult.BLOCKING_PENDING:
        print("blocking review finding pending attested resolution", file=sys.stderr)
        raise SystemExit(BLOCKING_PENDING_EXIT)
    if decision.result is GateResult.TRIAGE_REQUIRED:
        print("blocking low-confidence finding requires human triage", file=sys.stderr)
        raise SystemExit(TRIAGE_REQUIRED_EXIT)
    if decision.result is GateResult.PENDING_DIFF:
        print("pending diff evidence", file=sys.stderr)
        raise SystemExit(PENDING_DIFF_EXIT)


if __name__ == "__main__":
    main()
