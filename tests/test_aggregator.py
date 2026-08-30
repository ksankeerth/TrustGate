import pytest

from app.core.config import default_settings
from app.core.contracts import Decision, LayerResult
from app.scoring.aggregator import aggregate

LAYER_NAMES = ["face_match", "liveness", "deepfake", "injection"]


def make_layer(name: str, risk: float, confidence: float = 1.0, demonstrator: bool = False) -> LayerResult:
    return LayerResult(layer=name, risk=risk, confidence=confidence, ok=risk < 0.5, reason="test", demonstrator=demonstrator)


def uniform_layers(risk: float) -> list[LayerResult]:
    return [make_layer(name, risk) for name in LAYER_NAMES]


@pytest.mark.parametrize(
    "risk,expected_decision",
    [
        (0.0, Decision.ALLOW),
        (default_settings.allow_threshold, Decision.ALLOW),
        (default_settings.allow_threshold + 0.01, Decision.STEP_UP),
        (default_settings.step_up_threshold, Decision.STEP_UP),
        (default_settings.step_up_threshold + 0.01, Decision.DENY),
        (1.0, Decision.DENY),
    ],
)
def test_aggregate_decision_bands(risk, expected_decision):
    risk_score, decision, _reasons = aggregate(uniform_layers(risk))
    assert risk_score == pytest.approx(risk)
    assert decision == expected_decision


def test_reasons_empty_message_when_all_low_risk():
    _risk_score, _decision, reasons = aggregate(uniform_layers(0.1))
    assert reasons == ["all layers within acceptable risk range"]


def test_reasons_list_high_risk_layers():
    layers = uniform_layers(0.1)
    layers[0] = make_layer("face_match", 0.9)
    _risk_score, _decision, reasons = aggregate(layers)
    assert len(reasons) == 1
    assert "face_match" in reasons[0]


def test_demonstrator_layer_pulls_score_less_than_a_real_layer():
    # Same two risk values (0.9 high-risk, 0.1 low-risk) on the same pair of
    # layer names, but the high-risk one is flagged demonstrator in the
    # second case -- it should be down-weighted, pulling the overall score
    # down compared to when it counts as a full-weight real layer.
    real_high_risk = [
        make_layer("face_match", risk=0.9, demonstrator=False),
        make_layer("deepfake", risk=0.1, demonstrator=False),
    ]
    demonstrator_high_risk = [
        make_layer("face_match", risk=0.9, demonstrator=True),
        make_layer("deepfake", risk=0.1, demonstrator=False),
    ]

    real_score, _, _ = aggregate(real_high_risk)
    demonstrator_score, _, _ = aggregate(demonstrator_high_risk)

    assert demonstrator_score < real_score


def test_zero_confidence_layers_fall_back_to_zero_risk():
    layers = [make_layer(name, risk=0.9, confidence=0.0) for name in LAYER_NAMES]
    risk_score, decision, _reasons = aggregate(layers)
    assert risk_score == 0.0
    assert decision == Decision.ALLOW
