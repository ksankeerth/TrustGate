import asyncio
import logging
import time

from app.core.config import (
    LatencySettings,
    ResilienceSettings,
    default_deepfake_settings,
    default_face_match_settings,
    default_latency_settings,
    default_resilience_settings,
)
from app.core.contracts import Decision, FailPosture, LayerResult, VerifyResponse
from app.core.contracts import VerificationState as State
from app.layers.base import Layer, VerificationInput
from app.layers.deepfake import DeepfakeLayer, RealDeepfakeLayer
from app.layers.face_match import FaceMatchLayer, RealFaceMatchLayer
from app.layers.injection import InjectionLayer
from app.layers.liveness import LivenessLayer
from app.core.config import default_settings as aggregator_settings
from app.scoring.aggregator import aggregate, decide
from app.state.store import VerificationStateStore

logger = logging.getLogger(__name__)


def _build_deepfake_layer() -> Layer:
    if default_deepfake_settings.enabled:
        return RealDeepfakeLayer(default_deepfake_settings)
    return DeepfakeLayer()


def _build_face_match_layer() -> Layer:
    if default_face_match_settings.enabled:
        return RealFaceMatchLayer(default_face_match_settings)
    return FaceMatchLayer()


DEFAULT_SYNC_LAYERS: list[Layer] = [_build_face_match_layer(), LivenessLayer(), _build_deepfake_layer(), InjectionLayer()]


class Orchestrator:
    def __init__(
        self,
        state_store: VerificationStateStore,
        layers: list[Layer] | None = None,
        latency_settings: LatencySettings = default_latency_settings,
        resilience_settings: ResilienceSettings = default_resilience_settings,
    ) -> None:
        self._state_store = state_store
        self._layers = layers if layers is not None else DEFAULT_SYNC_LAYERS
        self._latency = latency_settings
        self._resilience = resilience_settings

    def _result_for_failed_layer(self, layer: Layer, error: BaseException) -> LayerResult:
        """Stand in for a layer that could not produce a result.

        Layers are expected to handle their own bad input and return a graded
        result; reaching here means something genuinely unexpected broke, so
        the configured posture decides whether that counts against the user.
        """
        fail_closed = self._resilience.layer_fail_posture is FailPosture.FAIL_CLOSED
        return LayerResult(
            layer=getattr(layer, "name", "unknown"),
            # Under FAIL_OPEN, confidence 0 gives the layer zero weight in the
            # aggregator, so it is excluded rather than scored as harmless.
            risk=1.0 if fail_closed else 0.0,
            confidence=1.0 if fail_closed else 0.0,
            ok=False,
            reason=(
                f"layer failed ({type(error).__name__}: {error}); "
                f"posture {self._resilience.layer_fail_posture.value}"
            ),
            detail={"failed": True, "error_type": type(error).__name__},
            demonstrator=False,
        )

    @staticmethod
    async def _run_timed(layer: Layer, verification_input: VerificationInput):
        """Run one layer and stamp its wall-clock cost onto the result.

        Timing lives here rather than in each layer so every layer is measured
        the same way, including the ones that do no work.
        """
        start = time.perf_counter()
        result = await layer.run(verification_input)
        result.duration_ms = round((time.perf_counter() - start) * 1000, 2)
        return result

    def _log_timing(self, layer_results, total_ms: float) -> None:
        breakdown = ", ".join(
            f"{r.layer}={r.duration_ms:.0f}ms" if r.duration_ms is not None else f"{r.layer}=failed"
            for r in layer_results
        )
        slowest = max((r.duration_ms or 0.0) for r in layer_results) if layer_results else 0.0
        serial = sum((r.duration_ms or 0.0) for r in layer_results)

        if total_ms > self._latency.hard_ceiling_ms:
            logger.error(
                "sync tier took %.0fms, OVER the %.0fms hard ceiling -- the calling flow will have failed: %s",
                total_ms, self._latency.hard_ceiling_ms, breakdown,
            )
        elif total_ms > self._latency.budget_ms:
            logger.warning(
                "sync tier took %.0fms, over the %.0fms budget (ceiling %.0fms): %s",
                total_ms, self._latency.budget_ms, self._latency.hard_ceiling_ms, breakdown,
            )
        else:
            logger.info(
                "sync tier %.0fms (slowest layer %.0fms, serial would be %.0fms): %s",
                total_ms, slowest, serial, breakdown,
            )

    async def run_sync_tier(self, verification_input: VerificationInput) -> VerifyResponse:
        started = time.perf_counter()
        # return_exceptions so one broken layer cannot take down the whole
        # request; each failure is converted per the configured posture.
        raw = await asyncio.gather(
            *(self._run_timed(layer, verification_input) for layer in self._layers),
            return_exceptions=True,
        )
        layer_results = []
        for layer, outcome in zip(self._layers, raw):
            if isinstance(outcome, BaseException):
                logger.exception(
                    "layer %s failed; applying %s",
                    getattr(layer, "name", "unknown"),
                    self._resilience.layer_fail_posture.value,
                    exc_info=outcome,
                )
                layer_results.append(self._result_for_failed_layer(layer, outcome))
            else:
                layer_results.append(outcome)
        total_ms = round((time.perf_counter() - started) * 1000, 2)
        self._log_timing(layer_results, total_ms)

        risk_score, decision, reasons = aggregate(list(layer_results))

        failed = [r.layer for r in layer_results if r.detail.get("failed")]
        if failed and self._resilience.layer_fail_posture is FailPosture.FAIL_CLOSED:
            # A weighted average lets three healthy layers dilute one that never
            # ran, which can still produce a clean ALLOW. Fail-closed means a
            # check that did not run cannot be waved through, so the score is
            # floored just into STEP_UP -- raise the score rather than override
            # the decision, so the two never disagree.
            floor = aggregator_settings.allow_threshold + 0.01
            if risk_score < floor:
                risk_score = floor
                decision = decide(risk_score, aggregator_settings)
            reasons.append(
                f"fail-closed: {', '.join(failed)} could not be evaluated, so this "
                "attempt cannot be approved without a step-up"
            )

        next_state = State.REJECTED if decision == Decision.DENY else State.PROVISIONAL
        state = self._state_store.transition(verification_input.user_ref, next_state)

        return VerifyResponse(
            user_ref=verification_input.user_ref,
            state=state,
            decision=decision,
            risk_score=risk_score,
            reasons=reasons,
            layers=list(layer_results),
            total_duration_ms=total_ms,
        )
