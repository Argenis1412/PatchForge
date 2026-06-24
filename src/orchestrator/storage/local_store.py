import os
from pathlib import Path

from .artifact_store import ArtifactStore, DurabilityLevel, WriteResult


class LocalArtifactStore(ArtifactStore):
    def __init__(self, base_path: Path):
        self._base = Path(base_path).resolve()

    def write(self, path: str, data: str) -> WriteResult:
        full_path = self._base / path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        tmp = full_path.with_suffix(full_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
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
        path = Path(ref)
        if path.is_absolute():
            return path.read_text(encoding="utf-8")
        return (self._base / ref).read_text(encoding="utf-8")

    def delete(self, ref: str) -> None:
        path = Path(ref)
        if not path.is_absolute():
            path = self._base / ref
        path.unlink(missing_ok=True)
