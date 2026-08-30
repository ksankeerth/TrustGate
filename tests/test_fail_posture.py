import pytest

from app.core.config import ResilienceSettings
from app.core.contracts import Decision, FailPosture, LayerResult
from app.layers.base import Layer, VerificationInput
from app.orchestrator import Orchestrator
from app.state.store import VerificationStateStore


class BoomLayer(Layer):
    name = "boom"

    async def run(self, verification_input):
        raise RuntimeError("layer bug")


class CleanLayer(Layer):
    def __init__(self, name: str, risk: float = 0.05) -> None:
        self.name = name
        self._risk = risk

    async def run(self, verification_input) -> LayerResult:
        return LayerResult(layer=self.name, risk=self._risk, confidence=1.0, ok=True, reason="fine")


def build(posture: FailPosture, layers):
    return Orchestrator(
        VerificationStateStore(),
        layers=layers,
        resilience_settings=ResilienceSettings(layer_fail_posture=posture),
    )


def mixed_layers():
    return [BoomLayer(), CleanLayer("a"), CleanLayer("b"), CleanLayer("c")]


@pytest.mark.asyncio
@pytest.mark.parametrize("posture", list(FailPosture))
async def test_a_raising_layer_never_fails_the_request(posture):
    """One broken layer must not take down the whole verification."""
    response = await build(posture, mixed_layers()).run_sync_tier(VerificationInput(user_ref="u"))

    assert len(response.layers) == 4
    assert response.decision in set(Decision)


@pytest.mark.asyncio
async def test_fail_closed_scores_the_broken_layer_as_maximum_risk():
    response = await build(FailPosture.FAIL_CLOSED, mixed_layers()).run_sync_tier(VerificationInput(user_ref="u"))

    boom = next(layer for layer in response.layers if layer.layer == "boom")
    assert boom.risk == 1.0
    assert boom.confidence == 1.0
    assert boom.ok is False


@pytest.mark.asyncio
async def test_fail_open_gives_the_broken_layer_zero_weight():
    """Excluded from scoring, not scored as harmless: confidence 0 means the
    aggregator gives it no weight at all.
    """
    response = await build(FailPosture.FAIL_OPEN, mixed_layers()).run_sync_tier(VerificationInput(user_ref="u"))

    boom = next(layer for layer in response.layers if layer.layer == "boom")
    assert boom.confidence == 0.0
    assert boom.ok is False


@pytest.mark.asyncio
async def test_posture_changes_the_decision_on_identical_input():
    """The whole point of the setting: same failure, different outcome."""
    closed = await build(FailPosture.FAIL_CLOSED, mixed_layers()).run_sync_tier(VerificationInput(user_ref="u"))
    opened = await build(FailPosture.FAIL_OPEN, mixed_layers()).run_sync_tier(VerificationInput(user_ref="u"))

    assert closed.risk_score > opened.risk_score
    assert opened.decision == Decision.ALLOW
    assert closed.decision != Decision.ALLOW


@pytest.mark.asyncio
async def test_failed_layer_reports_why_and_which_posture_applied():
    response = await build(FailPosture.FAIL_CLOSED, mixed_layers()).run_sync_tier(VerificationInput(user_ref="u"))

    boom = next(layer for layer in response.layers if layer.layer == "boom")
    assert "RuntimeError" in boom.reason
    assert "FAIL_CLOSED" in boom.reason
    assert boom.detail["failed"] is True


@pytest.mark.asyncio
async def test_healthy_layers_are_unaffected_by_a_neighbour_failing():
    response = await build(FailPosture.FAIL_CLOSED, mixed_layers()).run_sync_tier(VerificationInput(user_ref="u"))

    healthy = [layer for layer in response.layers if layer.layer != "boom"]
    assert len(healthy) == 3
    assert all(layer.ok and layer.duration_ms is not None for layer in healthy)


@pytest.mark.asyncio
async def test_every_layer_failing_denies_under_fail_closed():
    layers = [BoomLayer(), BoomLayer(), BoomLayer(), BoomLayer()]
    response = await build(FailPosture.FAIL_CLOSED, layers).run_sync_tier(VerificationInput(user_ref="u"))

    assert response.risk_score == 1.0
    assert response.decision == Decision.DENY


@pytest.mark.asyncio
async def test_every_layer_failing_under_fail_open_scores_nothing():
    """No layer carries weight, so there is no evidence either way. The
    aggregator's empty-weight fallback decides -- worth knowing this is what
    FAIL_OPEN degenerates to.
    """
    layers = [BoomLayer(), BoomLayer()]
    response = await build(FailPosture.FAIL_OPEN, layers).run_sync_tier(VerificationInput(user_ref="u"))

    assert response.risk_score == 0.0
    assert response.decision == Decision.ALLOW


@pytest.mark.asyncio
async def test_fail_closed_prevents_a_clean_allow_even_when_diluted():
    """One failed layer among three healthy ones averages out to ALLOW under a
    plain weighted mean. Fail-closed must not let a check that never ran be
    waved through, so the score is floored into STEP_UP.
    """
    response = await build(FailPosture.FAIL_CLOSED, mixed_layers()).run_sync_tier(VerificationInput(user_ref="u"))

    assert response.decision == Decision.STEP_UP
    assert any("could not be evaluated" in reason for reason in response.reasons)


@pytest.mark.asyncio
async def test_score_and_decision_never_disagree_after_the_floor():
    """The floor raises the score rather than overriding the verdict, so the
    two still tell the same story.
    """
    from app.core.config import default_settings
    from app.scoring.aggregator import decide

    response = await build(FailPosture.FAIL_CLOSED, mixed_layers()).run_sync_tier(VerificationInput(user_ref="u"))
    assert decide(response.risk_score, default_settings) == response.decision


@pytest.mark.asyncio
async def test_fail_open_still_allows_when_the_rest_are_clean():
    response = await build(FailPosture.FAIL_OPEN, mixed_layers()).run_sync_tier(VerificationInput(user_ref="u"))

    assert response.decision == Decision.ALLOW
    assert not any("could not be evaluated" in reason for reason in response.reasons)
