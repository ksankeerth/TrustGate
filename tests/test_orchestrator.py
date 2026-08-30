import asyncio
import time

import pytest

from app.core.contracts import Decision, LayerResult, VerificationState
from app.layers.base import Layer, VerificationInput
from app.orchestrator import Orchestrator
from app.state.store import VerificationStateStore

SLEEP_SECONDS = 0.1


class SleepyLayer(Layer):
    def __init__(self, name: str, risk: float) -> None:
        self.name = name
        self._risk = risk

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        await asyncio.sleep(SLEEP_SECONDS)
        return LayerResult(layer=self.name, risk=self._risk, confidence=1.0, ok=self._risk < 0.5, reason="sleepy stub", demonstrator=False)


def make_sleepy_layers(risk: float) -> list[Layer]:
    return [SleepyLayer(f"layer-{i}", risk) for i in range(4)]


@pytest.mark.asyncio
async def test_orchestrator_returns_verify_response_for_low_risk():
    orchestrator = Orchestrator(VerificationStateStore(), layers=make_sleepy_layers(0.1))
    response = await orchestrator.run_sync_tier(VerificationInput(user_ref="user-1"))

    assert response.decision == Decision.ALLOW
    assert response.state == VerificationState.PROVISIONAL
    assert len(response.layers) == 4


@pytest.mark.asyncio
async def test_orchestrator_rejects_on_high_risk():
    orchestrator = Orchestrator(VerificationStateStore(), layers=make_sleepy_layers(0.9))
    response = await orchestrator.run_sync_tier(VerificationInput(user_ref="user-1"))

    assert response.decision == Decision.DENY
    assert response.state == VerificationState.REJECTED


@pytest.mark.asyncio
async def test_orchestrator_runs_layers_concurrently():
    orchestrator = Orchestrator(VerificationStateStore(), layers=make_sleepy_layers(0.1))

    start = time.monotonic()
    await orchestrator.run_sync_tier(VerificationInput(user_ref="user-1"))
    elapsed = time.monotonic() - start

    num_layers = len(make_sleepy_layers(0.1))
    assert elapsed < SLEEP_SECONDS * num_layers  # would be ~4x SLEEP_SECONDS if run sequentially
    assert elapsed < SLEEP_SECONDS * 2  # should be close to a single layer's sleep time
