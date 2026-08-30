from datetime import datetime, timedelta, timezone

import pytest

from app.core.contracts import Challenge, LayerResult
from app.layers.base import VerificationInput
from app.layers.deepfake import DeepfakeLayer
from app.layers.face_match import FaceMatchLayer
from app.layers.injection import InjectionLayer

# Liveness is deliberately absent: it is no longer a hash-based stub but a real
# (if deliberately weak) demonstrator, covered by tests/test_liveness.py.
ALL_LAYERS = [FaceMatchLayer(), DeepfakeLayer(), InjectionLayer()]


def make_input() -> VerificationInput:
    challenge = Challenge(
        challenge_id="c-1",
        prompt_sequence=["blink"],
        nonce="fixed-nonce",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    return VerificationInput(
        user_ref="user-1",
        selfie=b"selfie-bytes",
        id_photo=b"id-photo-bytes",
        liveness_frames=[b"frame-1", b"frame-2"],
        challenge=challenge,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", ALL_LAYERS, ids=lambda layer: layer.name)
async def test_stub_returns_valid_schema_and_is_demonstrator(layer):
    result = await layer.run(make_input())
    assert isinstance(result, LayerResult)
    assert result.demonstrator is True
    assert result.layer == layer.name


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", ALL_LAYERS, ids=lambda layer: layer.name)
async def test_stub_is_deterministic_for_same_input(layer):
    first = await layer.run(make_input())
    second = await layer.run(make_input())
    assert first.risk == second.risk


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", ALL_LAYERS, ids=lambda layer: layer.name)
async def test_stub_changes_for_different_input(layer):
    baseline = await layer.run(make_input())

    changed_input = make_input()
    changed_input.selfie = b"different-selfie-bytes"
    changed_input.liveness_frames = [b"different-frame"]
    changed = await layer.run(changed_input)

    assert changed.risk != baseline.risk
