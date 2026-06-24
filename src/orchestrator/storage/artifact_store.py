from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel


class DurabilityLevel(StrEnum):
    LOCAL_ATOMIC = "LOCAL_ATOMIC"
    REMOTE_CONFIRMED = "REMOTE_CONFIRMED"
    REMOTE_EVENTUAL = "REMOTE_EVENTUAL"


class WriteResult(BaseModel):
    ref: str
    durability: DurabilityLevel


class ArtifactStore(ABC):
    @abstractmethod
    def write(self, path: str, data: str | bytes) -> WriteResult: ...

    @abstractmethod
    def read(self, ref: str) -> str: ...

    @abstractmethod
    def delete(self, ref: str) -> None: ...
