import asyncio
import io
import logging
import time
from pathlib import Path

from app.core.config import DEEPFAKE_MODEL_CHECKPOINTS, DeepfakeSettings, default_deepfake_settings
from app.core.contracts import LayerResult
from app.layers.base import Layer, VerificationInput, deterministic_unit_score

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DeepfakeLayer(Layer):
    """Deterministic mock used by default so the sync tier stays fast and
    dependency-free for tests. See RealDeepfakeLayer for the real classifier.
    """

    name = "deepfake"

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        risk = deterministic_unit_score(verification_input.selfie)
        return LayerResult(
            layer=self.name,
            risk=risk,
            confidence=0.5,
            ok=risk < 0.5,
            reason="stub: mock fake-probability score, no real classifier wired yet",
            detail={"selfie_present": verification_input.selfie is not None},
            demonstrator=True,
        )


_MODEL_CACHE: dict[tuple[str, str, str], tuple] = {}


def _resolve_cache_dir(cache_dir: str) -> str:
    """Resolve a relative cache_dir against the project root.

    Without this, the weights directory would be interpreted relative to the
    process's working directory, so starting the service from elsewhere would
    silently re-download the model into a different location.
    """
    path = Path(cache_dir)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return str(path)


def _load_model(checkpoint: str, cache_dir: str, device: str) -> tuple:
    """Load (and cache) the processor/model pair for a checkpoint.

    Imports transformers/torch lazily so importing this module never pulls
    in those heavy dependencies unless a RealDeepfakeLayer is actually built.

    Cached per (checkpoint, cache_dir, device): device is part of the key
    because the model is moved onto it at construction, so sharing one
    instance across devices would leave whichever layer was built first
    pointing at tensors on the wrong device.
    """
    from transformers import AutoImageProcessor, AutoModelForImageClassification

    resolved_cache_dir = _resolve_cache_dir(cache_dir)
    key = (checkpoint, resolved_cache_dir, device)
    if key not in _MODEL_CACHE:
        processor = AutoImageProcessor.from_pretrained(checkpoint, cache_dir=resolved_cache_dir)
        model = AutoModelForImageClassification.from_pretrained(checkpoint, cache_dir=resolved_cache_dir)
        model.eval().to(device)
        _MODEL_CACHE[key] = (processor, model)
    return _MODEL_CACHE[key]


def _find_fake_label_index(id2label: dict) -> int:
    """Normalize a model's native label order to the index meaning 'fake'.

    Both currently supported checkpoints happen to spell it "Fake" or
    "Deepfake" -- either way the substring match finds it without needing
    per-model special-casing. Falls back to whichever label doesn't look
    like "real" if no label contains "fake".
    """
    for idx, label in id2label.items():
        if "fake" in label.lower():
            return int(idx)
    return next((int(idx) for idx, label in id2label.items() if "real" not in label.lower()), 1)


class RealDeepfakeLayer(Layer):
    """Load-either SigLIP2/ViT deepfake classifier.

    Not wired into the default sync tier (see DeepfakeSettings.enabled) so
    the fast test suite and default app startup stay model-download-free;
    opt in explicitly for real inference.
    """

    name = "deepfake"

    def __init__(self, settings: DeepfakeSettings = default_deepfake_settings) -> None:
        self._settings = settings
        self._checkpoint = DEEPFAKE_MODEL_CHECKPOINTS[settings.model_choice]
        self._processor, self._model = _load_model(self._checkpoint, settings.cache_dir, settings.device)
        self._fake_index = _find_fake_label_index(self._model.config.id2label)

    def _predict(self, image_bytes: bytes) -> tuple[float, float, float]:
        import torch
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        inputs = self._processor(images=image, return_tensors="pt").to(self._settings.device)

        start = time.monotonic()
        with torch.no_grad():
            logits = self._model(**inputs).logits
        elapsed_ms = (time.monotonic() - start) * 1000

        probs = torch.softmax(logits, dim=-1)[0]
        fake_probability = probs[self._fake_index].item()
        confidence = probs.max().item()
        return fake_probability, confidence, elapsed_ms

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        if verification_input.selfie is None:
            # Full confidence, not 0: the aggregator scales layer weight by
            # confidence, so a 0 here would drop this layer out of the score
            # entirely rather than registering that it could not be run.
            return LayerResult(
                layer=self.name,
                risk=1.0,
                confidence=1.0,
                ok=False,
                reason="cannot screen for deepfakes: no selfie provided",
                demonstrator=False,
            )

        try:
            fake_probability, confidence, elapsed_ms = await asyncio.to_thread(self._predict, verification_input.selfie)
        except Exception:
            # Undecodable/corrupt image bytes and inference failures must not
            # escape: the orchestrator gathers layers without return_exceptions,
            # so an exception here would fail the whole /verify request.
            logger.exception("deepfake layer failed")
            return LayerResult(
                layer=self.name,
                risk=1.0,
                confidence=1.0,
                ok=False,
                reason="cannot screen for deepfakes: selfie could not be processed",
                demonstrator=False,
            )

        logger.info("deepfake layer (%s) inference took %.1fms", self._checkpoint, elapsed_ms)

        return LayerResult(
            layer=self.name,
            risk=fake_probability,
            confidence=confidence,
            ok=fake_probability < 0.5,
            reason=(
                f"{self._checkpoint}: fake_probability={fake_probability:.3f} "
                "(does not generalize to unseen generators -- treat as a signal, not a verdict)"
            ),
            detail={"checkpoint": self._checkpoint, "inference_ms": round(elapsed_ms, 1)},
            demonstrator=False,
        )
