import io
import logging

from app.core.config import InjectionSettings, default_injection_settings
from app.core.contracts import LayerResult
from app.layers.base import Layer, VerificationInput

logger = logging.getLogger(__name__)

LIMITATIONS = (
    "demonstrator, weakest layer here: infers injection from EXIF provenance, "
    "frame uniformity and sensor-noise heuristics, all of which are easily "
    "forged and frequently absent on legitimate captures -- real assurance "
    "requires client attestation, which this service cannot perform"
)

# EXIF tag numbers, used directly to avoid a lookup table for four fields.
_EXIF_MAKE = 271
_EXIF_MODEL = 272
_EXIF_SOFTWARE = 305
_EXIF_DATETIME = 306


def _open_image(image_bytes: bytes):
    from PIL import Image

    try:
        return Image.open(io.BytesIO(image_bytes))
    except Exception:
        return None


def _read_exif(image) -> dict:
    try:
        exif = image.getexif()
    except Exception:
        return {}
    return {tag: value for tag, value in exif.items()} if exif else {}


def sensor_noise_score(image, sample_size: int) -> float | None:
    """Mean absolute difference between horizontally adjacent pixels.

    A crude stand-in for sensor noise, measured on a centre crop at native
    resolution (downscaling would average the noise away). Real camera output
    is never perfectly smooth; rendered or synthesised frames often are.

    Weak in both directions: a genuine photo of a blank wall scores low, and
    any attacker can add noise. Treated as a hint, never a verdict.
    """
    try:
        grayscale = image.convert("L")
    except Exception:
        return None

    width, height = grayscale.size
    if width < 2 or height < 2:
        return None

    crop_width = min(sample_size, width)
    crop_height = min(sample_size, height)
    left = (width - crop_width) // 2
    top = (height - crop_height) // 2
    crop = grayscale.crop((left, top, left + crop_width, top + crop_height))

    pixels = crop.tobytes()
    total = 0
    comparisons = 0
    for row_start in range(0, crop_height * crop_width, crop_width):
        row = pixels[row_start : row_start + crop_width]
        for index in range(len(row) - 1):
            total += abs(row[index] - row[index + 1])
            comparisons += 1
    return total / comparisons if comparisons else None


def _provenance_findings(exif: dict, settings: InjectionSettings) -> list[tuple[float, str]]:
    findings: list[tuple[float, str]] = []

    software = str(exif.get(_EXIF_SOFTWARE, "")).strip().lower()
    matched = next((m for m in settings.suspicious_software_markers if m in software), None)
    if matched:
        findings.append((0.9, f"processing tag '{software}' matches a known encoder/editor/virtual-camera marker"))

    if not exif.get(_EXIF_MAKE) and not exif.get(_EXIF_MODEL):
        findings.append(
            (
                0.5,
                "no camera make/model metadata -- consistent with a synthetic or injected source, "
                "but equally with routine EXIF stripping by a legitimate client",
            )
        )
    elif not exif.get(_EXIF_DATETIME):
        findings.append((0.35, "camera metadata present but capture timestamp missing"))

    return findings


def _frame_findings(frames: list[bytes], settings: InjectionSettings) -> tuple[list[tuple[float, str]], dict]:
    findings: list[tuple[float, str]] = []
    detail: dict = {}
    if len(frames) < 2:
        return findings, detail

    if len({len(frame) for frame in frames}) == 1:
        findings.append(
            (0.7, "every frame has an identical byte length, which independently encoded camera frames rarely do")
        )

    dimensions = set()
    for frame in frames:
        image = _open_image(frame)
        if image is not None:
            dimensions.add(image.size)
    if len(dimensions) > 1:
        findings.append((0.6, f"frames disagree on dimensions within one capture: {sorted(dimensions)}"))
    if dimensions:
        detail["frame_dimensions"] = sorted(f"{w}x{h}" for w, h in dimensions)

    return findings, detail


class InjectionLayer(Layer):
    """Injection-detection demonstrator.

    Injection means bypassing the camera entirely -- a virtual camera, emulator
    or direct API feed -- as opposed to a presentation attack held up to a real
    lens. Detecting that from uploaded stills alone is close to a lost cause:
    the signals available server-side (EXIF provenance, frame uniformity,
    sensor noise) are all trivially forgeable and often missing from perfectly
    legitimate captures. Production systems solve this with client attestation
    instead, which is outside this service's reach.

    So this layer is scored accordingly: the lowest confidence of any layer and
    the highest baseline risk, both so the aggregator leans on it least. See
    LIMITATIONS.
    """

    name = "injection"

    def __init__(self, settings: InjectionSettings = default_injection_settings) -> None:
        self._settings = settings

    def _evaluate(self, verification_input: VerificationInput) -> tuple[list[tuple[float, str]], dict]:
        findings: list[tuple[float, str]] = []
        detail: dict = {}

        selfie = verification_input.selfie
        if selfie is None:
            return [(0.5, "no selfie to inspect for capture provenance")], detail

        image = _open_image(selfie)
        if image is None:
            return [(0.5, "selfie could not be decoded; capture provenance not assessed")], detail

        exif = _read_exif(image)
        detail["exif_present"] = bool(exif)
        findings += _provenance_findings(exif, self._settings)

        noise = sensor_noise_score(image, self._settings.noise_sample_size)
        if noise is None:
            findings.append((0.4, "sensor noise could not be measured"))
        else:
            detail["sensor_noise"] = round(noise, 3)
            if noise < self._settings.min_sensor_noise:
                findings.append(
                    (0.7, f"selfie is implausibly smooth for a camera sensor (noise={noise:.2f}); may be rendered")
                )

        frame_findings, frame_detail = _frame_findings(verification_input.liveness_frames, self._settings)
        findings += frame_findings
        detail.update(frame_detail)

        return findings, detail

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        try:
            findings, detail = self._evaluate(verification_input)
        except Exception:
            # Must not escape: the orchestrator gathers layers without
            # return_exceptions, so raising here would fail the whole request.
            logger.exception("injection layer failed")
            findings, detail = [(0.5, "capture provenance could not be assessed")], {}

        # Worst finding wins: each is an independent way a capture can look
        # injected, so the strongest signal should not be averaged away.
        risk = max([risk for risk, _ in findings], default=0.0)
        risk = max(risk, self._settings.baseline_risk)

        detail["frame_count"] = len(verification_input.liveness_frames)
        detail["findings"] = [note for _, note in findings]

        summary = "; ".join(note for _, note in findings) if findings else "no injection indicators found"
        return LayerResult(
            layer=self.name,
            risk=risk,
            confidence=self._settings.confidence,
            ok=risk <= self._settings.baseline_risk,
            reason=f"{summary} -- {LIMITATIONS}",
            detail=detail,
            demonstrator=True,
        )
