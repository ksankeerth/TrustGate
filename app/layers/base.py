import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.contracts import Challenge, LayerResult


@dataclass
class VerificationInput:
    user_ref: str
    selfie: bytes | None = None
    id_photo: bytes | None = None
    liveness_frames: list[bytes] = field(default_factory=list)
    challenge: Challenge | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Layer(ABC):
    name: str

    @abstractmethod
    async def run(self, verification_input: VerificationInput) -> LayerResult:
        ...


def deterministic_unit_score(*chunks: bytes | str | None) -> float:
    """Hash the given chunks into a stable float in [0, 1].

    Used by stub layers to produce a mock risk score that is stable for a
    given input (same bytes in -> same score out) without any real model.
    """
    hasher = hashlib.sha256()
    for chunk in chunks:
        if chunk is None:
            continue
        hasher.update(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    digest_int = int.from_bytes(hasher.digest()[:8], byteorder="big")
    return digest_int / float(2**64 - 1)
