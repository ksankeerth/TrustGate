import asyncio
import logging

import pytest

from app.core.config import LatencySettings, default_latency_settings
from app.core.contracts import LayerResult
from app.layers.base import Layer, VerificationInput
from app.orchestrator import Orchestrator
from app.state.store import VerificationStateStore

SLEEP_SECONDS = 0.10


class SleepyLayer(Layer):
    def __init__(self, name: str, seconds: float = SLEEP_SECONDS) -> None:
        self.name = name
        self._seconds = seconds

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        await asyncio.sleep(self._seconds)
        return LayerResult(layer=self.name, risk=0.1, confidence=1.0, ok=True, reason="sleepy")


def build(layers, latency=default_latency_settings):
    return Orchestrator(VerificationStateStore(), layers=layers, latency_settings=latency)


def four_sleepy():
    return [SleepyLayer(f"layer-{i}") for i in range(4)]


@pytest.mark.asyncio
async def test_every_layer_reports_its_own_duration():
    response = await build(four_sleepy()).run_sync_tier(VerificationInput(user_ref="u"))

    assert len(response.layers) == 4
    for layer in response.layers:
        assert layer.duration_ms is not None
        assert layer.duration_ms >= SLEEP_SECONDS * 1000 * 0.8


@pytest.mark.asyncio
async def test_total_duration_is_reported():
    response = await build(four_sleepy()).run_sync_tier(VerificationInput(user_ref="u"))
    assert response.total_duration_ms is not None
    assert response.total_duration_ms > 0


@pytest.mark.asyncio
async def test_total_is_far_below_the_serial_sum():
    """The concurrency guarantee, stated in the units callers care about:
    running four 100ms layers must cost ~100ms, not ~400ms.
    """
    response = await build(four_sleepy()).run_sync_tier(VerificationInput(user_ref="u"))

    serial = sum(layer.duration_ms for layer in response.layers)
    slowest = max(layer.duration_ms for layer in response.layers)

    assert response.total_duration_ms < serial * 0.6
    assert response.total_duration_ms < slowest * 2


@pytest.mark.asyncio
async def test_total_covers_the_slowest_layer():
    """One slow layer sets the floor for the whole tier."""
    layers = [SleepyLayer("fast", 0.02), SleepyLayer("slow", 0.20), SleepyLayer("fast-2", 0.02)]
    response = await build(layers).run_sync_tier(VerificationInput(user_ref="u"))

    assert response.total_duration_ms >= 200 * 0.8


@pytest.mark.asyncio
async def test_stays_within_the_configured_budget():
    response = await build(four_sleepy()).run_sync_tier(VerificationInput(user_ref="u"))
    assert response.total_duration_ms < default_latency_settings.budget_ms


@pytest.mark.asyncio
async def test_exceeding_the_budget_logs_a_warning(caplog):
    tight = LatencySettings(budget_ms=10.0, hard_ceiling_ms=20_000.0)
    with caplog.at_level(logging.WARNING, logger="app.orchestrator"):
        await build(four_sleepy(), tight).run_sync_tier(VerificationInput(user_ref="u"))

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("over the" in m for m in warnings), warnings


@pytest.mark.asyncio
async def test_exceeding_the_hard_ceiling_logs_an_error(caplog):
    """Past the ceiling the calling flow has already failed, so this is not a warning."""
    impossible = LatencySettings(budget_ms=1.0, hard_ceiling_ms=5.0)
    with caplog.at_level(logging.ERROR, logger="app.orchestrator"):
        await build(four_sleepy(), impossible).run_sync_tier(VerificationInput(user_ref="u"))

    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("hard ceiling" in m for m in errors), errors


@pytest.mark.asyncio
async def test_timing_survives_a_layer_that_reports_a_failure():
    """A layer returning a bad result is still measured, not skipped."""

    class FailingLayer(Layer):
        name = "failing"

        async def run(self, verification_input):
            await asyncio.sleep(0.02)
            return LayerResult(layer=self.name, risk=1.0, confidence=1.0, ok=False, reason="nope")

    response = await build([FailingLayer(), SleepyLayer("ok")]).run_sync_tier(VerificationInput(user_ref="u"))
    assert all(layer.duration_ms is not None for layer in response.layers)
