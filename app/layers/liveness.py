import hashlib
import hmac
import io
import logging
from datetime import datetime, timezone

from app.core.config import LivenessSettings, default_liveness_settings
from app.core.contracts import Challenge, LayerResult
from app.layers.base import Layer, VerificationInput

logger = logging.getLogger(__name__)

LIMITATIONS = (
    "demonstrator: checks challenge freshness, single use, frame binding and "
    "inter-frame variation only -- it does NOT verify the prompted actions were "
    "performed, and is not a certified presentation-attack detection control"
)


def compute_frame_binding(nonce: str, frames: list[bytes]) -> str:
    """HMAC that a client returns to bind its frames to a specific challenge.

    What this proves: the payload was assembled by something that held this
    challenge's nonce, so a wholesale replay of a previously captured payload
    (frames plus the binding issued for an older challenge) fails.

    What it does NOT prove: that the frames were captured live. The nonce is
    handed to the client in the clear, so anyone who can request a challenge
    can compute a valid binding over pre-recorded footage. Detecting that is
    the injection layer's job, not this one's.
    """
    return hmac.new(nonce.encode("utf-8"), b"".join(frames), hashlib.sha256).hexdigest()


def _as_aware(moment: datetime) -> datetime:
    """Treat a naive datetime as UTC so expiry comparison never raises."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _largest_consecutive_delta(frames: list[bytes], sample_size: int) -> float | None:
    """Mean per-pixel difference (0-255) between the most-different consecutive pair.

    Returns None if the frames cannot be decoded as images, so the caller can
    report "not assessed" rather than silently treating undecodable input as
    either motion or stillness.
    """
    from PIL import Image

    grayscale_frames = []
    for frame in frames:
        try:
            image = Image.open(io.BytesIO(frame)).convert("L").resize((sample_size, sample_size))
        except Exception:
            return None
        # tobytes() on an "L" image is one byte per pixel, in order -- the same
        # values getdata() yields, without its deprecation.
        grayscale_frames.append(image.tobytes())

    deltas = []
    for earlier, later in zip(grayscale_frames, grayscale_frames[1:]):
        total = sum(abs(a - b) for a, b in zip(earlier, later))
        deltas.append(total / len(earlier))
    return max(deltas) if deltas else None


def _challenge_findings(challenge: Challenge | None, metadata: dict) -> list[tuple[float, str]]:
    if challenge is None:
        return [(1.0, "no challenge bound to this attempt")]

    findings: list[tuple[float, str]] = []
    if datetime.now(timezone.utc) >= _as_aware(challenge.expires_at):
        findings.append((1.0, "challenge expired"))
    if metadata.get("challenge_first_use") is False:
        findings.append((1.0, "challenge already used (replayed attempt)"))
    return findings


def _binding_findings(challenge: Challenge | None, frames: list[bytes], metadata: dict) -> list[tuple[float, str]]:
    supplied = metadata.get("frame_binding")
    if supplied is None:
        return [(0.5, "no frame binding supplied; cannot confirm the frames were assembled for this challenge")]
    if challenge is None:
        return [(1.0, "frame binding supplied without a challenge to verify it against")]

    expected = compute_frame_binding(challenge.nonce, frames)
    if not hmac.compare_digest(expected, str(supplied)):
        return [(1.0, "frame binding does not match this challenge's nonce")]
    return []


def _motion_findings(frames: list[bytes], settings: LivenessSettings) -> tuple[list[tuple[float, str]], float | None]:
    if len(frames) < settings.min_frames:
        return [(0.8, f"only {len(frames)} frame(s) supplied; need at least {settings.min_frames}")], None

    delta = _largest_consecutive_delta(frames, settings.motion_sample_size)
    if delta is None:
        return [(0.5, "frames could not be decoded as images; motion not assessed")], None
    if delta < settings.min_pixel_delta:
        return [(0.9, f"consecutive frames are nearly identical (delta={delta:.2f}); likely a static image")], delta
    return [], delta


class LivenessLayer(Layer):
    """Randomized-prompt liveness demonstrator.

    Deliberately weak and self-reporting: it establishes that an attempt is
    tied to a fresh, single-use, server-issued challenge and that the frames
    are not one still image repeated, but it cannot tell whether the prompted
    actions were actually performed. See LIMITATIONS.

    Needs no model download, so unlike the deepfake and face-match layers it
    runs by default rather than behind an enable flag.
    """

    name = "liveness"

    def __init__(self, settings: LivenessSettings = default_liveness_settings) -> None:
        self._settings = settings

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        challenge = verification_input.challenge
        frames = verification_input.liveness_frames
        metadata = verification_input.metadata or {}

        findings = _challenge_findings(challenge, metadata)
        findings += _binding_findings(challenge, frames, metadata)
        motion_findings, delta = _motion_findings(frames, self._settings)
        findings += motion_findings

        # Worst finding wins: these are independent ways an attempt can be
        # bogus, so the weakest link sets the risk rather than being averaged
        # away by the checks that happened to pass.
        risk = max([r for r, _ in findings], default=0.0)
        risk = max(risk, self._settings.baseline_risk)

        detail = {
            "frame_count": len(frames),
            "challenge_present": challenge is not None,
            "binding_supplied": metadata.get("frame_binding") is not None,
            "findings": [note for _, note in findings],
        }
        if delta is not None:
            detail["largest_frame_delta"] = round(delta, 3)

        summary = "; ".join(note for _, note in findings) if findings else "challenge fresh, bound and frames vary"
        return LayerResult(
            layer=self.name,
            risk=risk,
            confidence=self._settings.confidence,
            ok=risk <= self._settings.baseline_risk,
            reason=f"{summary} -- {LIMITATIONS}",
            detail=detail,
            demonstrator=True,
        )
