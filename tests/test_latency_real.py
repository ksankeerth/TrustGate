"""Latency of the sync tier with the real model-backed layers loaded.

Marked slow: it downloads (or reads from cache) the deepfake checkpoint and
the facenet weights. Run with `pytest -m slow`.

Inputs are synthetic images, so the numbers measure the *pipeline*, not
recognition accuracy -- a noise image gives MTCNN nothing to detect, so
face_match short-circuits sooner than it would on a real capture.
"""

import io
import random

import pytest

from app.core.config import DeepfakeSettings, FaceMatchSettings
from app.layers.base import VerificationInput
from app.layers.deepfake import RealDeepfakeLayer
from app.layers.face_match import RealFaceMatchLayer
from app.layers.injection import InjectionLayer
from app.layers.liveness import LivenessLayer
from app.orchestrator import Orchestrator
from app.state.store import VerificationStateStore

pytestmark = pytest.mark.slow


def synthetic_jpeg(seed: int, size: int = 224) -> bytes:
    from PIL import Image

    rng = random.Random(seed)
    image = Image.new("RGB", (size, size))
    image.putdata([(rng.randint(0, 255),) * 3 for _ in range(size * size)])
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_real_sync_tier_latency_and_concurrency():
    layers = [
        RealFaceMatchLayer(FaceMatchSettings(enabled=True)),
        LivenessLayer(),
        RealDeepfakeLayer(DeepfakeSettings(enabled=True)),
        InjectionLayer(),
    ]
    orchestrator = Orchestrator(VerificationStateStore(), layers=layers)

    verification_input = VerificationInput(
        user_ref="latency-probe",
        selfie=synthetic_jpeg(1),
        id_photo=synthetic_jpeg(2),
        liveness_frames=[synthetic_jpeg(3), synthetic_jpeg(4)],
    )

    # First call pays one-off warm-up (lazy imports, first inference); measure
    # the second. A separate user_ref for the warm-up because a synthetic image
    # scores high enough to land in REJECTED, which is terminal.
    await orchestrator.run_sync_tier(
        VerificationInput(
            user_ref="latency-warmup",
            selfie=verification_input.selfie,
            id_photo=verification_input.id_photo,
            liveness_frames=verification_input.liveness_frames,
        )
    )
    response = await orchestrator.run_sync_tier(verification_input)

    serial = sum(layer.duration_ms for layer in response.layers)
    slowest = max(layer.duration_ms for layer in response.layers)

    print("\n  per-layer:")
    for layer in sorted(response.layers, key=lambda item: -item.duration_ms):
        print(f"    {layer.layer:<12} {layer.duration_ms:>8.1f} ms")
    print(f"    {'TOTAL':<12} {response.total_duration_ms:>8.1f} ms")
    print(f"    (serial would be {serial:.1f} ms; slowest layer {slowest:.1f} ms)")

    # Concurrency holds with the real layers, not just sleepy stubs.
    assert response.total_duration_ms < serial
    assert response.total_duration_ms < slowest * 1.5
