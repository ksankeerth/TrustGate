from app.core.contracts import LayerResult
from app.layers.base import Layer, VerificationInput, deterministic_unit_score


class LivenessLayer(Layer):
    name = "liveness"

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        nonce = verification_input.challenge.nonce if verification_input.challenge else None
        risk = deterministic_unit_score(*verification_input.liveness_frames, nonce)
        return LayerResult(
            layer=self.name,
            risk=risk,
            confidence=0.4,
            ok=risk < 0.5,
            reason="stub: mock challenge/frame binding, no real motion/blink heuristics wired yet",
            detail={"frame_count": len(verification_input.liveness_frames), "challenge_present": verification_input.challenge is not None},
            demonstrator=True,
        )
