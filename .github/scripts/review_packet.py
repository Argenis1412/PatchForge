"""Build a canonical v3 diff packet from named Git objects only."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from orchestrator.review_evidence import (
    DiffReviewPacket,
    TreeEntry,
    build_canonical_change_set,
    canonical_json,
)


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, stdout=subprocess.PIPE).stdout


def _tree(commit: str) -> tuple[TreeEntry, ...]:
    entries: list[TreeEntry] = []
    for raw in _git("ls-tree", "-r", "-z", commit).split(b"\0"):
        if not raw:
            continue
        metadata, path = raw.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split(" ")
        try:
            decoded_path = path.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("tree path is not UTF-8 representable") from error
        entries.append(
            TreeEntry(path=decoded_path, mode=mode, object_type=object_type, object_id=object_id)
        )
    return tuple(entries)


def _blobs(entries: tuple[TreeEntry, ...]) -> dict[str, bytes]:
    return {
        entry.object_id: _git("cat-file", "blob", entry.object_id)
        for entry in entries
        if entry.object_type == "blob"
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-head-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan_tree, head_tree = _tree(args.plan_head_sha), _tree(args.head_sha)
    blobs = _blobs(plan_tree) | _blobs(head_tree)
    changes, texts = build_canonical_change_set(plan_tree, head_tree, blobs)
    args.output.write_bytes(
        canonical_json(DiffReviewPacket(canonical_change_set=changes, text_deltas=texts))
    )


if __name__ == "__main__":
    main()
