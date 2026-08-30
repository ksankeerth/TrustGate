from pydantic import BaseModel


class AggregatorSettings(BaseModel):
    layer_weights: dict[str, float] = {
        "face_match": 1.0,
        "liveness": 0.6,
        "deepfake": 1.0,
        "injection": 0.5,
    }
    demonstrator_weight_multiplier: float = 0.5
    allow_threshold: float = 0.3
    step_up_threshold: float = 0.7
    reason_risk_threshold: float = 0.5


default_settings = AggregatorSettings()


class ChallengeSettings(BaseModel):
    prompt_pool: list[str] = [
        "blink",
        "turn_left",
        "turn_right",
        "smile",
        "nod",
        "open_mouth",
    ]
    sequence_length: int = 3
    ttl_seconds: int = 60


default_challenge_settings = ChallengeSettings()
