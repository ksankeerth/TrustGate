from app.core.contracts import LayerResult
from app.layers.base import Layer, VerificationInput, deterministic_unit_score


class InjectionLayer(Layer):
    name = "injection"

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        risk = deterministic_unit_score(*verification_input.liveness_frames)
        return LayerResult(
            layer=self.name,
            risk=risk,
            confidence=0.3,
            ok=risk < 0.5,
            reason="stub: mock stream-artifact score, no real injection heuristics wired yet",
            detail={"frame_count": len(verification_input.liveness_frames)},
            demonstrator=True,
        )
