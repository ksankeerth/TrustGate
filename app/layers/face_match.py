from app.core.contracts import LayerResult
from app.layers.base import Layer, VerificationInput, deterministic_unit_score


class FaceMatchLayer(Layer):
    name = "face_match"

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        risk = deterministic_unit_score(verification_input.selfie, verification_input.id_photo)
        return LayerResult(
            layer=self.name,
            risk=risk,
            confidence=0.5,
            ok=risk < 0.5,
            reason="stub: mock similarity score, no real face embedding model wired yet",
            detail={"selfie_present": verification_input.selfie is not None, "id_photo_present": verification_input.id_photo is not None},
            demonstrator=True,
        )
