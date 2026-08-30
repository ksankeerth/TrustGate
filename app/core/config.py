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


class LivenessSettings(BaseModel):
    min_frames: int = 2
    # Mean per-pixel difference (0-255 scale) between the most-different pair of
    # consecutive frames. Below this they are effectively the same picture.
    min_pixel_delta: float = 2.0
    # Frames are downscaled to this square size before comparison, for speed.
    motion_sample_size: int = 64
    # Risk floor even when every check passes: this layer is a weak demonstrator,
    # so a clean result is never strong evidence of a live human.
    baseline_risk: float = 0.2
    # Confidence is capped low across the board for the same reason.
    confidence: float = 0.35


default_liveness_settings = LivenessSettings()

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


class FaceMatchSettings(BaseModel):
    enabled: bool = False  # off by default: keeps the default sync-tier wiring fast/deterministic for tests
    pretrained: str = "vggface2"  # or "casia-webface" (both from facenet-pytorch, MIT licensed)
    similarity_threshold: float = 0.5  # cosine similarity at/above which the pair is treated as a match
    similarity_floor: float = 0.0  # cosine similarity at/below which risk saturates at 1.0
    device: str = "cpu"
    cache_dir: str = ".cache/torch"


default_face_match_settings = FaceMatchSettings()
