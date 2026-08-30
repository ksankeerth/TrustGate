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
