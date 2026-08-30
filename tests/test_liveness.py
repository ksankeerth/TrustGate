import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from app.core.config import default_liveness_settings as settings
from app.core.contracts import Challenge
from app.layers.base import VerificationInput
from app.layers.liveness import LivenessLayer, compute_frame_binding

LAYER = LivenessLayer()


def make_frame(brightness: int, size: int = 96) -> bytes:
    """A solid-grey JPEG. Two frames of differing brightness stand in for motion."""
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), (brightness, brightness, brightness)).save(buffer, format="JPEG")
    return buffer.getvalue()


MOVING_FRAMES = [make_frame(40), make_frame(200)]
STATIC_FRAMES = [make_frame(120), make_frame(120)]


def make_challenge(expires_in_seconds: int = 60, nonce: str = "test-nonce") -> Challenge:
    return Challenge(
        challenge_id="c-1",
        prompt_sequence=["blink", "nod"],
        nonce=nonce,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
    )


_UNSET = object()


async def run_layer(challenge=_UNSET, frames=None, bind=False, first_use=True, binding=None):
    # Sentinel, not None: callers need to pass challenge=None explicitly to
    # exercise the missing-challenge path.
    challenge = make_challenge() if challenge is _UNSET else challenge
    frames = MOVING_FRAMES if frames is None else frames

    metadata = {"challenge_first_use": first_use}
    if bind and challenge is not None:
        metadata["frame_binding"] = compute_frame_binding(challenge.nonce, frames)
    if binding is not None:
        metadata["frame_binding"] = binding

    return await LAYER.run(
        VerificationInput(user_ref="u", liveness_frames=frames, challenge=challenge, metadata=metadata)
    )


@pytest.mark.asyncio
async def test_fresh_bound_varying_frames_are_the_best_case():
    result = await run_layer(bind=True)
    assert result.ok is True
    assert result.risk == pytest.approx(settings.baseline_risk)
    assert result.demonstrator is True


@pytest.mark.asyncio
async def test_clean_pass_never_reports_zero_risk():
    """A demonstrator this weak must not present a clean run as strong evidence."""
    result = await run_layer(bind=True)
    assert result.risk > 0.0


@pytest.mark.asyncio
async def test_reason_always_states_the_layer_is_a_demonstrator():
    for kwargs in ({"bind": True}, {"frames": STATIC_FRAMES}, {"challenge": None}):
        result = await run_layer(**kwargs)
        assert "demonstrator" in result.reason
        assert "not a certified" in result.reason


@pytest.mark.asyncio
async def test_expired_challenge_is_maximum_risk():
    result = await run_layer(challenge=make_challenge(expires_in_seconds=-1), bind=True)
    assert result.risk == 1.0
    assert result.ok is False
    assert any("expired" in note for note in result.detail["findings"])


@pytest.mark.asyncio
async def test_replayed_challenge_is_maximum_risk():
    result = await run_layer(bind=True, first_use=False)
    assert result.risk == 1.0
    assert any("replayed" in note for note in result.detail["findings"])


@pytest.mark.asyncio
async def test_missing_challenge_is_maximum_risk():
    result = await run_layer(challenge=None)
    assert result.risk == 1.0
    assert any("no challenge" in note for note in result.detail["findings"])


@pytest.mark.asyncio
async def test_binding_from_a_different_nonce_is_rejected():
    """A payload replayed from an earlier challenge carries that challenge's
    binding, which must not validate against the current nonce.
    """
    stale_binding = compute_frame_binding("some-other-nonce", MOVING_FRAMES)
    result = await run_layer(binding=stale_binding)
    assert result.risk == 1.0
    assert any("does not match" in note for note in result.detail["findings"])


@pytest.mark.asyncio
async def test_binding_over_different_frames_is_rejected():
    """The binding must cover the frames actually submitted, not just the nonce."""
    binding_for_other_frames = compute_frame_binding("test-nonce", STATIC_FRAMES)
    result = await run_layer(frames=MOVING_FRAMES, binding=binding_for_other_frames)
    assert result.risk == 1.0


@pytest.mark.asyncio
async def test_absent_binding_is_riskier_than_a_valid_one():
    bound = await run_layer(bind=True)
    unbound = await run_layer(bind=False)
    assert unbound.risk > bound.risk
    assert any("no frame binding" in note for note in unbound.detail["findings"])


@pytest.mark.asyncio
async def test_identical_frames_are_flagged_as_a_static_image():
    result = await run_layer(frames=STATIC_FRAMES, bind=True)
    assert result.risk > settings.baseline_risk
    assert any("nearly identical" in note for note in result.detail["findings"])
    assert result.detail["largest_frame_delta"] < settings.min_pixel_delta


@pytest.mark.asyncio
async def test_varying_frames_score_lower_than_identical_ones():
    moving = await run_layer(frames=MOVING_FRAMES, bind=True)
    static = await run_layer(frames=STATIC_FRAMES, bind=True)
    assert moving.risk < static.risk


@pytest.mark.asyncio
async def test_too_few_frames_is_flagged():
    result = await run_layer(frames=[make_frame(40)], bind=True)
    assert result.risk > settings.baseline_risk
    assert any("at least" in note for note in result.detail["findings"])


@pytest.mark.asyncio
async def test_undecodable_frames_are_reported_as_not_assessed():
    """Non-image bytes must not be silently treated as either motion or stillness."""
    result = await run_layer(frames=[b"not-an-image", b"also-not-an-image"], bind=True)
    assert any("could not be decoded" in note for note in result.detail["findings"])
    assert "largest_frame_delta" not in result.detail


@pytest.mark.asyncio
async def test_confidence_stays_low_for_a_demonstrator():
    result = await run_layer(bind=True)
    assert result.confidence <= 0.5
