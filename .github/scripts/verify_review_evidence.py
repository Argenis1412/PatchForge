"""Verify current PR review artifacts before the gate accepts them."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

from orchestrator.review_evidence import (
    DiffReviewSubject,
    PlanReviewSubject,
    PlanScopePacket,
    ReviewRecord,
    sha256_digest,
)


def _run(*args: str, text: bool = True) -> str | bytes:
    result = subprocess.run(args, check=True, capture_output=True, text=text)
    return result.stdout


def _records(repo: str, name: str, expected_head: str, signer: str) -> list[ReviewRecord]:
    raw = _run("gh", "api", "--paginate", "--slurp", f"/repos/{repo}/actions/artifacts?name={name}")
    artifacts = [artifact for page in json.loads(raw) for artifact in page["artifacts"]]
    records: list[ReviewRecord] = []
    for artifact in artifacts:
        if artifact["expired"] or artifact["workflow_run"]["head_sha"] != expected_head:
            continue
        archive = _run(
            "gh", "api", f"/repos/{repo}/actions/artifacts/{artifact['id']}/zip", text=False
        )
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "evidence.zip"
            archive_path.write_bytes(archive)
            with zipfile.ZipFile(archive_path) as bundle:
                if bundle.namelist() != ["review-record.json"]:
                    continue
                record_path = Path(directory) / "review-record.json"
                record_path.write_bytes(bundle.read("review-record.json"))
            _run(
                "gh",
                "attestation",
                "verify",
                str(record_path),
                "--repo",
                repo,
                "--signer-workflow",
                signer,
            )
            records.append(
                ReviewRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
            )
    return records


def _select(records: list[ReviewRecord]) -> ReviewRecord:
    if not records:
        raise SystemExit("no current verified evidence artifact")
    return max(
        records, key=lambda record: (record.emitted_at, record.workflow_run_id, record.record_id)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--plan-packet", type=Path, required=True)
    parser.add_argument("--diff-packet", type=Path, required=True)
    args = parser.parse_args()
    packet = PlanScopePacket.model_validate_json(args.plan_packet.read_text(encoding="utf-8"))
    if packet.base_sha != args.base_sha:
        raise SystemExit("plan packet base SHA is stale")
    plan = _select(
        _records(
            args.repo,
            f"review-evidence-plan_review-{args.pr}",
            packet.plan_head_sha,
            f"{args.repo}/.github/workflows/review-plan.yml",
        )
    )
    if not isinstance(plan.subject, PlanReviewSubject) or plan.subject != PlanReviewSubject(
        base_sha=args.base_sha, plan_head_sha=packet.plan_head_sha, packet_digest=packet.digest
    ):
        raise SystemExit("plan evidence subject does not match the current admission")
    diff = _select(
        _records(
            args.repo,
            f"review-evidence-diff_review-{args.pr}",
            args.head_sha,
            f"{args.repo}/.github/workflows/review-diff.yml",
        )
    )
    expected = DiffReviewSubject(
        base_sha=args.base_sha,
        plan_head_sha=packet.plan_head_sha,
        head_sha=args.head_sha,
        diff_digest=sha256_digest(json.loads(args.diff_packet.read_text(encoding="utf-8"))),
    )
    if not isinstance(diff.subject, DiffReviewSubject) or diff.subject != expected:
        raise SystemExit("diff evidence subject does not match the current synchronize event")


if __name__ == "__main__":
    main()
