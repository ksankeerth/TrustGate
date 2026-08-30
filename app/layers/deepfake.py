from app.core.contracts import LayerResult
from app.layers.base import Layer, VerificationInput, deterministic_unit_score


class DeepfakeLayer(Layer):
    name = "deepfake"

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        risk = deterministic_unit_score(verification_input.selfie)
        return LayerResult(
            layer=self.name,
            risk=risk,
            confidence=0.5,
            ok=risk < 0.5,
            reason="stub: mock fake-probability score, no real classifier wired yet",
            detail={"selfie_present": verification_input.selfie is not None},
            demonstrator=True,
        )
