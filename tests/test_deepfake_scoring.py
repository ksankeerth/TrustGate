from unittest.mock import patch

import pytest

from app.core.config import DeepfakeSettings
from app.layers.base import VerificationInput
from app.layers.deepfake import RealDeepfakeLayer, _find_fake_label_index


def make_layer() -> RealDeepfakeLayer:
    """Build the layer without loading (or downloading) any real model."""
    fake_model = type("FakeModel", (), {"config": type("Config", (), {"id2label": {0: "Real", 1: "Fake"}})()})()
    with patch("app.layers.deepfake._load_model", return_value=(object(), fake_model)):
        return RealDeepfakeLayer(DeepfakeSettings(enabled=True))


@pytest.mark.parametrize(
    "id2label,expected",
    [
        ({0: "Fake", 1: "Real"}, 0),
        ({0: "Real", 1: "Fake"}, 1),
        ({0: "Realism", 1: "Deepfake"}, 1),
        ({0: "Deepfake", 1: "Realism"}, 0),
    ],
)
def test_fake_label_index_normalizes_native_label_order(id2label, expected):
    """The two supported checkpoints ship opposite label orders, so the layer
    must resolve 'which index means fake' rather than assuming a position.
    """
    assert _find_fake_label_index(id2label) == expected


@pytest.mark.asyncio
async def test_missing_selfie_is_a_full_confidence_failure():
    """Missing input must not be scored as a zero-weight no-op: the aggregator
    multiplies layer weight by confidence, so confidence 0 would drop this
    layer out of the score rather than registering that it could not run.
    """
    layer = make_layer()
    result = await layer.run(VerificationInput(user_ref="u", selfie=None))

    assert result.ok is False
    assert result.confidence > 0.0


@pytest.mark.asyncio
async def test_undecodable_image_returns_a_result_instead_of_raising():
    """The orchestrator gathers layers without return_exceptions, so an
    exception escaping here would fail the entire /verify request.
    """
    layer = make_layer()
    with patch.object(layer, "_predict", side_effect=OSError("cannot identify image file")):
        result = await layer.run(VerificationInput(user_ref="u", selfie=b"not-an-image"))

    assert result.ok is False
    assert result.risk == 1.0
