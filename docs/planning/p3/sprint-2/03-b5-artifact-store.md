# B5 — Pluggable Artifact Store

## Goal

Artifacts must survive worker container destruction. The `ArtifactStore` interface provides pluggable backends (local, S3) while keeping storage in the `storage/` layer — cleanly separated from schemas.

---

## Current State

### `src/orchestrator/workspace.py:81-91` — All artifacts written to local filesystem

```python
def write_artifact(self, run_id: str, filename: str, content: str) -> Path:
    self.ensure_run_exists(run_id)
    run_dir = self.run_dir(run_id)
    path = run_dir / validate_filename(filename)
    path.write_text(content, encoding="utf-8")
    return path
```

Only local filesystem. In Docker, filesystem is ephemeral — artifacts vanish with the container.

### No abstraction layer

`WorkspaceManager.write_artifact()` is hardcoded to `Path.write_text()`. There is no `ArtifactStore` interface.

---

## Changes

### 1. Create `src/orchestrator/storage/artifact_store.py`

```python
"""Pluggable artifact backend with explicit durability contract."""

from abc import ABC, abstractmethod
from enum import Enum, auto
from pathlib import Path
from pydantic import BaseModel


# Inherit from str so Pydantic v2 serializes as string ("LOCAL_ATOMIC") not int
class DurabilityLevel(str, Enum):
    LOCAL_ATOMIC = "LOCAL_ATOMIC"       # os.replace + fsync — for WAL/apply.json
    REMOTE_CONFIRMED = "REMOTE_CONFIRMED"   # S3 200 OK with x-amz-request-id
    REMOTE_EVENTUAL = "REMOTE_EVENTUAL"    # async, best-effort (never for operational data)


class WriteResult(BaseModel):
    ref: str                     # URL, URN, or path
    durability: DurabilityLevel  # what the caller can rely on


class ArtifactStore(ABC):
    """Contract: write() must not return until the store confirms durability."""

    @abstractmethod
    def write(self, path: str, data: str | bytes) -> WriteResult: ...

    @abstractmethod
    def read(self, ref: str) -> str: ...

    @abstractmethod
    def delete(self, ref: str) -> None: ...
```

### 2. Create `src/orchestrator/storage/local_store.py`

```python
"""Local filesystem implementation — default for CLI usage."""

import os
from pathlib import Path
from .artifact_store import ArtifactStore, DurabilityLevel, WriteResult


class LocalArtifactStore(ArtifactStore):
    def __init__(self, base_path: Path):
        self._base = Path(base_path).resolve()

    def write(self, path: str, data: str | bytes) -> WriteResult:
        full_path = self._base / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Use canonical atomic write logic with fsync. See 00-README.md §Canonical Patterns
        tmp = full_path.with_suffix(full_path.suffix + ".tmp")
        if isinstance(data, str):
            with tmp.open("w", encoding="utf-8") as f:
                f.write(data)
                f.flush()                  # flush Python buffer → OS buffer cache
                os.fsync(f.fileno())       # force OS buffer cache → physical disk
        else:
            with tmp.open("wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, full_path)
        if os.name == "posix":
            dir_fd = os.open(str(full_path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)           # persist directory entry for crash-safe rename
            finally:
                os.close(dir_fd)
        
        return WriteResult(
            ref=str(full_path),
            durability=DurabilityLevel.LOCAL_ATOMIC,
        )

    def read(self, ref: str) -> str:
        path = Path(ref)
        if path.is_absolute():
            return path.read_text(encoding="utf-8")     # legacy absolute path
        return (self._base / ref).read_text(encoding="utf-8")  # canonical ref

    def delete(self, ref: str) -> None:
        Path(ref).unlink(missing_ok=True)
```

### 3. Update `WorkspaceManager` to delegate to store

`src/orchestrator/workspace.py`:

```python
class WorkspaceManager:
    def __init__(self, workspace_path: Path, store: Optional[ArtifactStore] = None):
        self.root = Path(workspace_path).resolve()
        self.store = store or LocalArtifactStore(self.root / "runs")
        # ... rest of init ...

    def write_artifact(self, run_id: str, name: str, data: str) -> str:
        """Delegate to configured store. Returns the ref."""
        return self.store.write(f"{run_id}/{name}", data).ref

    def read_artifact(self, run_id: str, name: str) -> str:
        # The store is responsible for resolving the canonical ref to a local path (self.root / runs / ref) or a remote key.
        # Never pass absolute paths to the store interface.
        ref = f"{run_id}/{name}"
        return self.store.read(ref)
```

### 4. PR body assembly becomes URL-based

```python
def assemble_pr_body(run_dir: Path, store_base_url: str, goal: str, run_id: str) -> str:
    return f"""
## PatchForge: {goal}

**Run ID:** {run_id}

### Artifacts
- [patch.diff]({store_base_url}/{run_id}/patch.diff)
- [validation.json]({store_base_url}/{run_id}/validation.json)
- [apply.json]({store_base_url}/{run_id}/apply.json)
- [risk_gate.json]({store_base_url}/{run_id}/risk_gate.json)
"""
```

### 5. WAL bypasses ArtifactStore (invariant)

The WAL write (`apply.json` with `status: "applying"`) is never delegated to `ArtifactStore`. It always writes directly to local filesystem. This is documented in the `ArtifactStore` contract and enforced in code.

### 6. Decomment TODO-B5 in WorkspaceManager (workspace.py)

Update `write_run_json()`, `write_verdict()`, `write_experiment()` to write
to the ArtifactStore in addition to the local path:

```python
# Before (B2 left this commented):
# TODO-B5: store.write(f"{run_id}/run.json", run_meta.model_dump_json(indent=2))

# After (uncomment and wire the store):
def write_run_json(self, run_id: str, run_meta: RunMetadata) -> None:
    local = self.run_dir(run_id) / "run.json"
    local.write_text(run_meta.model_dump_json(indent=2), encoding="utf-8")  # keep local copy
    self.store.write(f"{run_id}/run.json",                 # B5: now active
                     run_meta.model_dump_json(indent=2))

# Apply the same dual-write pattern to write_verdict() and write_experiment().
```

---

## Files to Create/Modify

- **NEW** `src/orchestrator/storage/artifact_store.py` — `ArtifactStore` ABC + `DurabilityLevel` + `WriteResult`
- **NEW** `src/orchestrator/storage/local_store.py` — `LocalArtifactStore` implementation
- `src/orchestrator/workspace.py` — Accept `ArtifactStore`, delegate `write_artifact`
- `src/orchestrator/schemas/artifacts.py` — No changes needed (storage != schema)

---

## Acceptance Criteria

- [ ] Artifacts survive worker container destruction (via pluggable store)
- [ ] PR body references artifacts by URL (never inlined)
- [ ] Local store remains default for CLI usage (backward compatible)
- [ ] `ArtifactStore` is in `storage/`, not `schemas/` — clean layering
- [ ] WAL bypasses ArtifactStore — always writes to local filesystem

---

## Test skeleton (create before running pytest)

Create `tests/test_artifact_store.py` with these cases:
```python
def test_local_store_atomic_write():
    """Verify LocalArtifactStore uses os.replace for atomic writes."""
    pass

def test_local_store_bytes_and_str():
    """Verify LocalArtifactStore handles both str and bytes correctly."""
    pass
```

## Verification

```bash
pytest tests/test_artifact_store.py -v
pytest tests/test_workspace.py -v

# Manual: LocalArtifactStore round-trip
python -c "
from orchestrator.storage.local_store import LocalArtifactStore
from pathlib import Path
store = LocalArtifactStore(Path('/tmp/test-store'))
result = store.write('test/hello.txt', 'world')
assert result.durability.name == 'LOCAL_ATOMIC'
data = store.read(result.ref)
assert data == 'world'
print('Store round-trip OK')
"
```

## Rollback

```bash
git rm src/orchestrator/storage/artifact_store.py src/orchestrator/storage/local_store.py
git checkout -- src/orchestrator/workspace.py
```
