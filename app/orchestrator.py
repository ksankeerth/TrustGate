import asyncio

from app.core.config import default_deepfake_settings
from app.core.contracts import Decision, VerifyResponse
from app.core.contracts import VerificationState as State
from app.layers.base import Layer, VerificationInput
from app.layers.deepfake import DeepfakeLayer, RealDeepfakeLayer
from app.layers.face_match import FaceMatchLayer
from app.layers.injection import InjectionLayer
from app.layers.liveness import LivenessLayer
from app.scoring.aggregator import aggregate
from app.state.store import VerificationStateStore


def _build_deepfake_layer() -> Layer:
    if default_deepfake_settings.enabled:
        return RealDeepfakeLayer(default_deepfake_settings)
    return DeepfakeLayer()


DEFAULT_SYNC_LAYERS: list[Layer] = [FaceMatchLayer(), LivenessLayer(), _build_deepfake_layer(), InjectionLayer()]


class Orchestrator:
    def __init__(self, state_store: VerificationStateStore, layers: list[Layer] | None = None) -> None:
        self._state_store = state_store
        self._layers = layers if layers is not None else DEFAULT_SYNC_LAYERS

    async def run_sync_tier(self, verification_input: VerificationInput) -> VerifyResponse:
        layer_results = await asyncio.gather(*(layer.run(verification_input) for layer in self._layers))
        risk_score, decision, reasons = aggregate(list(layer_results))

        next_state = State.REJECTED if decision == Decision.DENY else State.PROVISIONAL
        state = self._state_store.transition(verification_input.user_ref, next_state)

        return VerifyResponse(
            user_ref=verification_input.user_ref,
            state=state,
            decision=decision,
            risk_score=risk_score,
            reasons=reasons,
            layers=list(layer_results),
        )
