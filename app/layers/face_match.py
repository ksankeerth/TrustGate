import asyncio
import io
import logging
import os
import time
from pathlib import Path

from app.core.config import FaceMatchSettings, default_face_match_settings
from app.core.contracts import LayerResult
from app.layers.base import Layer, VerificationInput, deterministic_unit_score

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FaceMatchLayer(Layer):
    """Deterministic mock used by default so the sync tier stays fast and
    dependency-free for tests. See RealFaceMatchLayer for the real embeddings.
    """

    name = "face_match"

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        risk = deterministic_unit_score(verification_input.selfie, verification_input.id_photo)
        return LayerResult(
            layer=self.name,
            risk=risk,
            confidence=0.5,
            ok=risk < 0.5,
            reason="stub: mock similarity score, no real face embedding model wired yet",
            detail={"selfie_present": verification_input.selfie is not None, "id_photo_present": verification_input.id_photo is not None},
            demonstrator=True,
        )


def similarity_to_risk(similarity: float, threshold: float, floor: float) -> float:
    """Map cosine similarity to risk, anchored so `similarity == threshold` is exactly 0.5.

    Above the threshold risk falls toward 0 (clear match); below it risk climbs
    toward 1, saturating at `floor`. Anchoring to the threshold is what keeps
    `ok` and `risk` telling the same story: a pair that fails the threshold
    always scores >= 0.5, so it can never land in the aggregator's ALLOW band
    or be filtered out of the response as too low-risk to mention. A raw
    (1 - similarity) / 2 mapping does neither -- it puts a failing pair at
    ~0.25 (ALLOW) and makes DENY unreachable for L2-normalized embeddings,
    whose similarities never approach -1.
    """
    if similarity >= threshold:
        span = max(1e-6, 1.0 - threshold)
        risk = 0.5 * (1.0 - (similarity - threshold) / span)
    else:
        span = max(1e-6, threshold - floor)
        risk = 0.5 + 0.5 * ((threshold - similarity) / span)
    return max(0.0, min(1.0, risk))


_MODEL_CACHE: dict[tuple[str, str, str], tuple] = {}


def _resolve_cache_dir(cache_dir: str) -> str:
    """Resolve a relative cache_dir against the project root.

    Without this, TORCH_HOME would be interpreted relative to the process's
    working directory, so starting the service from elsewhere would silently
    re-download ~112MB of weights into a different location.
    """
    path = Path(cache_dir)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return str(path)


def _load_model(pretrained: str, cache_dir: str, device: str) -> tuple:
    """Load (and cache) the MTCNN detector + InceptionResnetV1 embedder.

    Imports the vendored facenet_pytorch subset lazily so importing this
    module never pulls in torch unless a RealFaceMatchLayer is actually
    built. Points the weight cache at a project-local directory (TORCH_HOME)
    rather than the default ~/.cache/torch. See app/layers/_vendor/facenet_pytorch/README.md
    for why this is vendored instead of an ordinary dependency.

    Cached per (pretrained, cache_dir, device): device is part of the key
    because both models are moved onto it at construction, so sharing one
    instance across devices would leave whichever layer was built first
    pointing at tensors on the wrong device.
    """
    resolved_cache_dir = _resolve_cache_dir(cache_dir)
    os.environ["TORCH_HOME"] = resolved_cache_dir

    import torch

    from app.layers._vendor.facenet_pytorch import InceptionResnetV1, MTCNN

    key = (pretrained, resolved_cache_dir, device)
    if key not in _MODEL_CACHE:
        torch_device = torch.device(device)
        detector = MTCNN(image_size=160, margin=0, post_process=True, device=torch_device)
        embedder = InceptionResnetV1(pretrained=pretrained).eval().to(torch_device)
        _MODEL_CACHE[key] = (detector, embedder)
    return _MODEL_CACHE[key]


class RealFaceMatchLayer(Layer):
    """Real face-embedding similarity: MTCNN detects/crops/aligns each face,
    InceptionResnetV1 (pretrained on VGGFace2 or CASIA-WebFace) embeds it,
    and cosine similarity between the selfie and ID-photo embeddings maps to
    risk (low similarity = high risk).

    Not wired into the default sync tier (see FaceMatchSettings.enabled) so
    the fast test suite and default app startup stay model-download-free;
    opt in explicitly for real inference.
    """

    name = "face_match"

    def __init__(self, settings: FaceMatchSettings = default_face_match_settings) -> None:
        self._settings = settings
        self._detector, self._embedder = _load_model(settings.pretrained, settings.cache_dir, settings.device)

    def _embed(self, image_bytes: bytes) -> tuple:
        """Return (embedding, detection_probability), or (None, None) if no face."""
        import torch
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        face_tensor, detection_probability = self._detector(image, return_prob=True)
        if face_tensor is None:
            return None, None

        with torch.no_grad():
            embedding = self._embedder(face_tensor.unsqueeze(0).to(self._settings.device))
        return embedding[0], detection_probability

    def _predict(self, selfie_bytes: bytes, id_photo_bytes: bytes) -> tuple:
        """Return (similarity, detection_probability, elapsed_ms).

        similarity is None when a face could not be detected in either image.
        """
        import torch

        start = time.monotonic()
        selfie_embedding, selfie_probability = self._embed(selfie_bytes)
        id_embedding, id_probability = self._embed(id_photo_bytes)
        elapsed_ms = (time.monotonic() - start) * 1000

        if selfie_embedding is None or id_embedding is None:
            return None, None, elapsed_ms

        similarity = torch.nn.functional.cosine_similarity(selfie_embedding, id_embedding, dim=0).item()
        # Weakest-link detection quality: a comparison is only as trustworthy
        # as the less confidently detected of the two faces.
        detection_probability = min(selfie_probability, id_probability)
        return similarity, detection_probability, elapsed_ms

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        if verification_input.selfie is None or verification_input.id_photo is None:
            # Report this as a failure to verify, at full confidence, rather
            # than a low-confidence pass: the aggregator scales layer weight by
            # confidence, so a confidence of 0 here would drop face match out
            # of the score entirely and let a request with no ID photo be
            # approved on the strength of the other layers alone.
            return LayerResult(
                layer=self.name,
                risk=1.0,
                confidence=1.0,
                ok=False,
                reason="cannot verify identity: both selfie and id_photo are required for face match",
                detail={
                    "selfie_present": verification_input.selfie is not None,
                    "id_photo_present": verification_input.id_photo is not None,
                },
                demonstrator=False,
            )

        try:
            similarity, detection_probability, elapsed_ms = await asyncio.to_thread(
                self._predict, verification_input.selfie, verification_input.id_photo
            )
        except Exception:
            # Undecodable/corrupt image bytes and inference failures must not
            # escape: the orchestrator gathers layers without return_exceptions,
            # so an exception here would fail the whole /verify request.
            logger.exception("face_match layer failed")
            return LayerResult(
                layer=self.name,
                risk=1.0,
                confidence=1.0,
                ok=False,
                reason="cannot verify identity: selfie or id_photo could not be processed",
                demonstrator=False,
            )

        logger.info("face_match layer (%s) inference took %.1fms", self._settings.pretrained, elapsed_ms)

        if similarity is None:
            return LayerResult(
                layer=self.name,
                risk=1.0,
                # Not 1.0: a missed detection can mean a poor-quality photo
                # rather than a genuine mismatch, but it is still a failure to
                # verify and must carry real weight.
                confidence=0.9,
                ok=False,
                reason="cannot verify identity: no face detected in selfie and/or id_photo",
                detail={"inference_ms": round(elapsed_ms, 1)},
                demonstrator=False,
            )

        risk = similarity_to_risk(
            similarity,
            self._settings.similarity_threshold,
            self._settings.similarity_floor,
        )
        # Confidence reflects how good the measurement was (face-detection
        # quality), NOT how far the verdict sits from the threshold. Peaking
        # confidence away from the boundary would mean the most ambiguous
        # comparisons -- exactly the ones that should drive STEP_UP -- get the
        # least weight in the aggregator.
        confidence = max(0.0, min(1.0, float(detection_probability)))

        return LayerResult(
            layer=self.name,
            risk=risk,
            confidence=confidence,
            ok=similarity >= self._settings.similarity_threshold,
            reason=(
                f"facenet/{self._settings.pretrained}: cosine_similarity={similarity:.3f} "
                f"threshold={self._settings.similarity_threshold} "
                f"detection_probability={confidence:.3f}"
            ),
            detail={
                "similarity": round(similarity, 4),
                "detection_probability": round(confidence, 4),
                "inference_ms": round(elapsed_ms, 1),
            },
            demonstrator=False,
        )
