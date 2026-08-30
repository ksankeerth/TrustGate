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

# Load-either real deepfake checkpoints: both are Apache-2.0, ungated, and
# expose a "fake"-containing label somewhere in id2label so the layer can
# normalize native label order without per-model special-casing.
DEEPFAKE_MODEL_CHECKPOINTS: dict[str, str] = {
    "vit": "prithivMLmods/Deep-Fake-Detector-v2-Model",
    "siglip2": "prithivMLmods/Deepfake-Detect-Siglip2",
}


class DeepfakeSettings(BaseModel):
    enabled: bool = False  # off by default: keeps the default sync-tier wiring fast/deterministic for tests
    model_choice: str = "vit"
    device: str = "cpu"
    cache_dir: str = ".cache/huggingface"


default_deepfake_settings = DeepfakeSettings()
