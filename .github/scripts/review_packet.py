"""Build a canonical v3 diff packet from named Git objects only."""

from __future__ import annotations

import argparse
import io
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


def _changed_blob_ids(
    plan_tree: tuple[TreeEntry, ...], head_tree: tuple[TreeEntry, ...]
) -> tuple[str, ...]:
    plan = {entry.path: entry for entry in plan_tree}
    head = {entry.path: entry for entry in head_tree}
    changed = {
        entry.object_id
        for entries, other in ((plan_tree, head), (head_tree, plan))
        for entry in entries
        if entry.object_type == "blob" and other.get(entry.path) != entry
    }
    return tuple(sorted(changed))


def _blobs(object_ids: tuple[str, ...]) -> dict[str, bytes]:
    if not object_ids:
        return {}
    process = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    request = "".join(f"{object_id}\n" for object_id in object_ids).encode("ascii")
    output, _ = process.communicate(request)
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, process.args)
    stream = io.BytesIO(output)
    blobs: dict[str, bytes] = {}
    for requested_id in object_ids:
        header = stream.readline().decode("ascii").rstrip("\n")
        parts = header.split(" ")
        if len(parts) != 3 or parts[1] != "blob":
            raise ValueError("missing blob bytes")
        actual_id, _, size_text = parts
        size = int(size_text)
        content = stream.read(size)
        if actual_id != requested_id or len(content) != size or stream.read(1) != b"\n":
            raise ValueError("missing blob bytes")
        blobs[requested_id] = content
    return blobs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-head-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan_tree, head_tree = _tree(args.plan_head_sha), _tree(args.head_sha)
    blobs = _blobs(_changed_blob_ids(plan_tree, head_tree))
    changes, texts = build_canonical_change_set(plan_tree, head_tree, blobs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(
        canonical_json(DiffReviewPacket(canonical_change_set=changes, text_deltas=texts))
    )


if __name__ == "__main__":
    main()
