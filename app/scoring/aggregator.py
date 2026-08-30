from app.core.config import AggregatorSettings, default_settings
from app.core.contracts import Decision, LayerResult


def aggregate(
    layer_results: list[LayerResult],
    settings: AggregatorSettings = default_settings,
) -> tuple[float, Decision, list[str]]:
    """Combine sync-tier layer results into a single risk score, decision, and reasons.

    Each layer's contribution is weighted by its configured base weight,
    scaled down for demonstrator layers, and scaled by the layer's own
    reported confidence -- so a low-confidence or demonstrator layer moves
    the overall score less than a confident, production-grade one.
    """
    weighted_sum = 0.0
    weight_total = 0.0
    reasons: list[str] = []

    for result in layer_results:
        base_weight = settings.layer_weights.get(result.layer, 1.0)
        demonstrator_factor = settings.demonstrator_weight_multiplier if result.demonstrator else 1.0
        effective_weight = base_weight * demonstrator_factor * result.confidence

        weighted_sum += effective_weight * result.risk
        weight_total += effective_weight

        if result.risk >= settings.reason_risk_threshold:
            reasons.append(f"{result.layer}: {result.reason} (risk={result.risk:.2f})")

    risk_score = weighted_sum / weight_total if weight_total > 0 else 0.0
    decision = decide(risk_score, settings)

    if not reasons:
        reasons.append("all layers within acceptable risk range")

    return risk_score, decision, reasons


def decide(risk_score: float, settings: AggregatorSettings = default_settings) -> Decision:
    """Map a risk score onto a decision band.

    Kept separate so any caller that adjusts a score afterwards derives the
    decision the same way, rather than the score and the verdict drifting
    apart.
    """
    if risk_score <= settings.allow_threshold:
        return Decision.ALLOW
    if risk_score <= settings.step_up_threshold:
        return Decision.STEP_UP
    return Decision.DENY
