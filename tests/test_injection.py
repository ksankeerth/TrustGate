import io
import random

import pytest
from PIL import Image

from app.core.config import default_injection_settings as settings
from app.layers.base import VerificationInput
from app.layers.injection import InjectionLayer, sensor_noise_score

LAYER = InjectionLayer()


def noisy_image(size: int = 160, seed: int = 1) -> Image.Image:
    """Stands in for a real camera capture: per-pixel noise, as a sensor produces."""
    rng = random.Random(seed)
    image = Image.new("RGB", (size, size))
    image.putdata([(rng.randint(0, 255),) * 3 for _ in range(size * size)])
    return image


def smooth_image(size: int = 160, shade: int = 128) -> Image.Image:
    """Stands in for a rendered/synthesised frame: perfectly flat, no sensor noise."""
    return Image.new("RGB", (size, size), (shade,) * 3)


def to_jpeg(image: Image.Image, exif: Image.Exif | None = None) -> bytes:
    buffer = io.BytesIO()
    if exif is not None:
        image.save(buffer, format="JPEG", exif=exif, quality=95)
    else:
        image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def camera_exif(software: str | None = None) -> Image.Exif:
    exif = Image.Exif()
    exif[271] = "DemoPhone"  # Make
    exif[272] = "DemoPhone 15"  # Model
    exif[306] = "2026:08:30 10:00:00"  # DateTime
    if software is not None:
        exif[305] = software
    return exif


GENUINE_CAPTURE = to_jpeg(noisy_image(), camera_exif())
STRIPPED_CAPTURE = to_jpeg(noisy_image())
RENDERED_CAPTURE = to_jpeg(smooth_image(), camera_exif())
VIRTUAL_CAMERA_CAPTURE = to_jpeg(noisy_image(), camera_exif(software="ManyCam Virtual Webcam 8.0"))


async def run_layer(selfie=GENUINE_CAPTURE, frames=None):
    return await LAYER.run(
        VerificationInput(user_ref="u", selfie=selfie, liveness_frames=frames or [])
    )


@pytest.mark.asyncio
async def test_genuine_looking_capture_is_the_best_case():
    result = await run_layer()
    assert result.ok is True
    assert result.risk == pytest.approx(settings.baseline_risk)
    assert result.detail["findings"] == []


@pytest.mark.asyncio
async def test_clean_pass_never_reports_zero_risk():
    """This layer is too weak for a clean run to count as evidence of nothing wrong."""
    result = await run_layer()
    assert result.risk > 0.0


@pytest.mark.asyncio
async def test_reason_always_states_the_layer_is_a_demonstrator():
    for selfie in (GENUINE_CAPTURE, RENDERED_CAPTURE, VIRTUAL_CAMERA_CAPTURE):
        result = await run_layer(selfie=selfie)
        assert result.demonstrator is True
        assert "demonstrator" in result.reason
        assert "client attestation" in result.reason


@pytest.mark.asyncio
async def test_confidence_is_the_lowest_of_any_layer():
    result = await run_layer()
    assert result.confidence <= 0.25


@pytest.mark.asyncio
async def test_virtual_camera_software_tag_scores_riskier_than_a_genuine_capture():
    genuine = await run_layer(selfie=GENUINE_CAPTURE)
    injected = await run_layer(selfie=VIRTUAL_CAMERA_CAPTURE)

    assert injected.risk > genuine.risk
    assert any("virtual-camera marker" in note for note in injected.detail["findings"])


@pytest.mark.asyncio
async def test_rendered_image_scores_riskier_than_a_noisy_capture():
    genuine = await run_layer(selfie=GENUINE_CAPTURE)
    rendered = await run_layer(selfie=RENDERED_CAPTURE)

    assert rendered.risk > genuine.risk
    assert any("implausibly smooth" in note for note in rendered.detail["findings"])


@pytest.mark.asyncio
async def test_missing_camera_metadata_is_flagged_but_hedged():
    """Stripped EXIF is a weak signal, so the finding must say so rather than
    presenting a routinely-benign condition as evidence of an attack.
    """
    result = await run_layer(selfie=STRIPPED_CAPTURE)
    assert result.risk > settings.baseline_risk

    note = next(n for n in result.detail["findings"] if "no camera make/model" in n)
    assert "EXIF stripping" in note


@pytest.mark.asyncio
async def test_identical_frame_byte_lengths_are_flagged():
    frame = to_jpeg(smooth_image(shade=100), camera_exif())
    result = await run_layer(frames=[frame, frame])
    assert any("identical byte length" in note for note in result.detail["findings"])


@pytest.mark.asyncio
async def test_mismatched_frame_dimensions_are_flagged():
    result = await run_layer(
        frames=[
            to_jpeg(noisy_image(size=160, seed=1), camera_exif()),
            to_jpeg(noisy_image(size=96, seed=2), camera_exif()),
        ]
    )
    assert any("disagree on dimensions" in note for note in result.detail["findings"])


@pytest.mark.asyncio
async def test_undecodable_selfie_is_reported_not_crashed():
    result = await run_layer(selfie=b"not-an-image")
    assert result.risk >= settings.baseline_risk
    assert any("could not be decoded" in note for note in result.detail["findings"])


@pytest.mark.asyncio
async def test_missing_selfie_is_reported_not_crashed():
    result = await run_layer(selfie=None)
    assert result.risk >= settings.baseline_risk
    assert any("no selfie" in note for note in result.detail["findings"])


def test_noise_score_separates_rendered_from_sensor_like_images():
    assert sensor_noise_score(smooth_image(), settings.noise_sample_size) < settings.min_sensor_noise
    assert sensor_noise_score(noisy_image(), settings.noise_sample_size) > settings.min_sensor_noise


def test_noise_score_returns_none_for_an_unmeasurably_small_image():
    assert sensor_noise_score(Image.new("RGB", (1, 1)), settings.noise_sample_size) is None
