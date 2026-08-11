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
    "LEGACY_MANIFEST_SCHEMA_VERSION",
    "ArtifactHash",
    "AuditManifest",
    "LegacyAuditManifest",
]

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

LEGACY_MANIFEST_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 2


class ArtifactHash(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str
    size_bytes: int


class AuditManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_schema_version: Literal[2]
    run_id: str
    patchforge_version: str
    bundle_created_at: datetime
    commit_anchor: str
    artifacts: list[ArtifactHash]
    run_metadata: dict
    export_profile: Literal["full", "redacted"]
    omitted_artifacts: list[str]


class LegacyAuditManifest(BaseModel):
    """The immutable AuditManifest@1 wire contract for historical bundles."""

    model_config = ConfigDict(extra="forbid")

    manifest_schema_version: Literal[1]
    run_id: str
    patchforge_version: str
    bundle_created_at: datetime
    commit_anchor: str
    artifacts: list[ArtifactHash]
    run_metadata: dict
