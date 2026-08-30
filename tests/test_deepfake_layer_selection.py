from unittest.mock import patch

from app.core.config import DeepfakeSettings
from app.layers.deepfake import DeepfakeLayer, RealDeepfakeLayer
from app.orchestrator import _build_deepfake_layer


def test_default_settings_select_the_stub_layer():
    layer = _build_deepfake_layer()
    assert isinstance(layer, DeepfakeLayer)
    assert not isinstance(layer, RealDeepfakeLayer)


def test_enabled_settings_select_the_real_layer():
    # Mock RealDeepfakeLayer so this stays fast/network-free while still
    # proving the gating logic picks it when enabled=True.
    enabled_settings = DeepfakeSettings(enabled=True)
    with patch("app.orchestrator.default_deepfake_settings", enabled_settings), patch(
        "app.orchestrator.RealDeepfakeLayer"
    ) as mock_real_layer:
        _build_deepfake_layer()
        mock_real_layer.assert_called_once_with(enabled_settings)
