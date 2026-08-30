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


class InjectionSettings(BaseModel):
    # Substrings matched case-insensitively against EXIF Software/processing
    # tags. Presence proves the image passed through one of these, not that an
    # injection occurred -- plenty of benign pipelines re-encode.
    suspicious_software_markers: list[str] = [
        "ffmpeg",
        "obs",
        "manycam",
        "epoccam",
        "droidcam",
        "virtual",
        "screen capture",
        "screenrecord",
        "photoshop",
        "gimp",
        "stable diffusion",
        "midjourney",
    ]
    # Mean absolute adjacent-pixel gradient. Real sensor output is noisy; a
    # value under this is implausibly smooth for a camera capture.
    min_sensor_noise: float = 1.0
    noise_sample_size: int = 128
    # Higher floor than liveness: server-side injection detection is weaker
    # still, so even a clean pass carries meaningful residual risk.
    baseline_risk: float = 0.3
    # The lowest confidence of any layer, by design.
    confidence: float = 0.2


default_injection_settings = InjectionSettings()


class ThunderIdSettings(BaseModel):
    # Off by default so the service runs standalone; the tests and the default
    # app never reach for a network.
    enabled: bool = False
    base_url: str = "https://localhost:8090"
    client_id: str = "TRUSTGATE"
    client_secret: str = "trustgate-local-client-secret"
    # RFC 8707 resource indicator. ThunderID rejects token requests without it
    # ("invalid_target"); the value is the System resource server's identifier.
    resource: str = "https://localhost:8090/mcp"
    scope: str = "system"
    # Which ThunderID user attribute a TrustGate user_ref is matched against.
    # Set to "id" to treat user_ref as the ThunderID user id directly.
    user_lookup_attribute: str = "username"
    attribute_name: str = "verification_status"
    # ThunderID serves HTTPS with a self-signed certificate locally.
    verify_tls: bool = False
    timeout_seconds: float = 10.0
    max_attempts: int = 3
    retry_backoff_seconds: float = 0.5
    # Refresh a token this many seconds before it actually expires.
    token_expiry_margin_seconds: float = 30.0


default_thunderid_settings = ThunderIdSettings()

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
