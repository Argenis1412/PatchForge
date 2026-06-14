---
title: Replace hardcoded schema_version default with CURRENT_SCHEMA_VERSION constant
severity: low
labels: refactor, schema
---
## Problem

In `src/orchestrator/schemas/artifacts.py`, the `schema_version` field on
`RunMetadata` uses a hardcoded integer literal `1` as its default value (line 51).
The module already defines `CURRENT_SCHEMA_VERSION: int = 1` at line 34 for
exactly this purpose. The two values are currently in sync but only by
coincidence — a future version bump that updates line 34 would silently leave
the field default stale.

## Required Change

**File:** `src/orchestrator/schemas/artifacts.py`
**Line:** 51
**Before:** `schema_version: int = 1`
**After:** `schema_version: int = CURRENT_SCHEMA_VERSION`

No import required. `CURRENT_SCHEMA_VERSION` is defined in the same module
at line 34 and is already in scope at the field definition site.

## Scope

- Exactly 1 line changed in exactly 1 file
- No other files require modification
- No new constants, imports, or validators needed
- `CURRENT_SCHEMA_VERSION` itself is not modified

## Acceptance Criteria

- `RunMetadata().schema_version` equals `CURRENT_SCHEMA_VERSION` at runtime
- No integer literal remains as the `schema_version` field default
- `ruff check .` — 0 errors
- `pytest` — all existing tests pass without modification
