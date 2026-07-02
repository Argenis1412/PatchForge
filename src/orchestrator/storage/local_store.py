import os
from pathlib import Path

from .artifact_store import ArtifactStore, DurabilityLevel, WriteResult


class LocalArtifactStore(ArtifactStore):
    def __init__(self, base_path: Path):
        self._base = Path(base_path).resolve()

    def _resolve_and_validate(self, path: str) -> Path:
        candidate = Path(path)
        resolved = candidate.resolve() if candidate.is_absolute() else (self._base / path).resolve()
        if not resolved.is_relative_to(self._base):
            raise ValueError(f"Path {path!r} resolves outside store base {self._base}")
        return resolved

    def write(self, path: str, data: str) -> WriteResult:
        full_path = self._resolve_and_validate(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)

        tmp = full_path.with_suffix(full_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, full_path)
        if os.name == "posix":
            dir_fd = os.open(str(full_path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

        return WriteResult(
            ref=str(full_path),
            durability=DurabilityLevel.LOCAL_ATOMIC,
        )

    def read(self, ref: str) -> str:
        path = self._resolve_and_validate(ref)
        return path.read_text(encoding="utf-8")

    def delete(self, ref: str) -> None:
        path = self._resolve_and_validate(ref)
        path.unlink(missing_ok=True)
