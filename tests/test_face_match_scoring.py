from unittest.mock import patch

import pytest

from app.core.config import FaceMatchSettings, default_settings
from app.layers.base import VerificationInput
from app.layers.face_match import RealFaceMatchLayer, similarity_to_risk

THRESHOLD = 0.5
FLOOR = 0.0


def make_layer(**overrides) -> RealFaceMatchLayer:
    """Build the layer without loading (or downloading) any real model."""
    settings = FaceMatchSettings(enabled=True, **overrides)
    with patch("app.layers.face_match._load_model", return_value=(object(), object())):
        return RealFaceMatchLayer(settings)


def test_risk_is_half_exactly_at_the_threshold():
    assert similarity_to_risk(THRESHOLD, THRESHOLD, FLOOR) == pytest.approx(0.5)


def test_risk_saturates_at_and_below_the_floor():
    assert similarity_to_risk(FLOOR, THRESHOLD, FLOOR) == pytest.approx(1.0)
    assert similarity_to_risk(-0.5, THRESHOLD, FLOOR) == pytest.approx(1.0)


def test_risk_is_zero_for_a_perfect_match():
    assert similarity_to_risk(1.0, THRESHOLD, FLOOR) == pytest.approx(0.0)


def test_risk_decreases_monotonically_as_similarity_rises():
    similarities = [-0.2, 0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0]
    risks = [similarity_to_risk(s, THRESHOLD, FLOOR) for s in similarities]
    assert risks == sorted(risks, reverse=True)


@pytest.mark.parametrize("similarity", [0.499, 0.4, 0.3, 0.2, 0.0, -0.5])
def test_failing_similarity_never_lands_in_the_allow_band(similarity):
    """A pair that fails the threshold must not score as ALLOW.

    Guards the mismatch that a raw (1 - similarity) / 2 mapping produces,
    where ok=False could still come back under the ALLOW threshold.
    """
    risk = similarity_to_risk(similarity, THRESHOLD, FLOOR)
    assert risk > default_settings.allow_threshold


@pytest.mark.parametrize("similarity", [0.499, 0.4, 0.3, 0.2, 0.0, -0.5])
def test_failing_similarity_always_surfaces_its_reason(similarity):
    """Failing pairs must clear reason_risk_threshold so the API explains why."""
    risk = similarity_to_risk(similarity, THRESHOLD, FLOOR)
    assert risk >= default_settings.reason_risk_threshold


def test_clear_impostor_reaches_the_deny_band():
    """DENY must be reachable: it is not under a raw (1 - similarity) / 2 mapping,
    which needs a cosine below -0.4 that L2-normalized embeddings never produce.
    """
    risk = similarity_to_risk(0.15, THRESHOLD, FLOOR)
    assert risk > default_settings.step_up_threshold


def test_clear_match_reaches_the_allow_band():
    risk = similarity_to_risk(0.85, THRESHOLD, FLOOR)
    assert risk <= default_settings.allow_threshold


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selfie,id_photo",
    [(b"selfie", None), (None, b"id"), (None, None)],
)
async def test_missing_image_is_a_full_confidence_failure(selfie, id_photo):
    """Missing inputs must not be scored as a zero-weight no-op.

    The aggregator multiplies layer weight by confidence, so confidence 0 here
    would drop face match out of the score and let a request with no ID photo
    pass on the other layers alone.
    """
    layer = make_layer()
    result = await layer.run(VerificationInput(user_ref="u", selfie=selfie, id_photo=id_photo))

    assert result.ok is False
    assert result.risk == 1.0
    assert result.confidence > 0.0


@pytest.mark.asyncio
async def test_undecodable_image_returns_a_result_instead_of_raising():
    """The orchestrator gathers layers without return_exceptions, so an
    exception escaping here would fail the entire /verify request.
    """
    layer = make_layer()
    with patch.object(layer, "_predict", side_effect=OSError("cannot identify image file")):
        result = await layer.run(VerificationInput(user_ref="u", selfie=b"not-an-image", id_photo=b"also-not"))

    assert result.ok is False
    assert result.risk == 1.0
