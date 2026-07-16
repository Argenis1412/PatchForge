"""Audit bundle manifest schema. See docs/planning/p4/04-audit-bundle-export.md.

The manifest is a terminal derived artifact, not an inter-stage DTO — round-trip
stability (Invariant #2) does not apply. ``run_metadata`` is a structural mirror
of ``RunMetadata.model_dump(mode="json")`` and is intentionally not re-validated
against the live ``RunMetadata`` schema on verify (see planning doc, "manifest is
a structural mirror, not an enumerated schema").
"""

from __future__ import annotations

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "ArtifactHash",
    "AuditManifest",
]

from datetime import datetime

from pydantic import BaseModel, ConfigDict

MANIFEST_SCHEMA_VERSION = 1


class ArtifactHash(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str
    size_bytes: int


class AuditManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_schema_version: int
    run_id: str
    patchforge_version: str
    bundle_created_at: datetime
    commit_anchor: str
    artifacts: list[ArtifactHash]
    run_metadata: dict
