from unittest.mock import patch

from app.core.config import FaceMatchSettings
from app.layers.face_match import FaceMatchLayer, RealFaceMatchLayer
from app.orchestrator import _build_face_match_layer


def test_default_settings_select_the_stub_layer():
    layer = _build_face_match_layer()
    assert isinstance(layer, FaceMatchLayer)
    assert not isinstance(layer, RealFaceMatchLayer)


def test_enabled_settings_select_the_real_layer():
    # Mock RealFaceMatchLayer so this stays fast/network-free while still
    # proving the gating logic picks it when enabled=True.
    enabled_settings = FaceMatchSettings(enabled=True)
    with patch("app.orchestrator.default_face_match_settings", enabled_settings), patch(
        "app.orchestrator.RealFaceMatchLayer"
    ) as mock_real_layer:
        _build_face_match_layer()
        mock_real_layer.assert_called_once_with(enabled_settings)
