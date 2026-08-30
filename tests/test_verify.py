import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.contracts import Challenge, Decision, VerificationState
from app.layers.base import VerificationInput
from app.main import app
from app.orchestrator import DEFAULT_SYNC_LAYERS
from app.scoring.aggregator import aggregate

client = TestClient(app)

SELFIE_BYTES = b"selfie-fixture-bytes"
ID_PHOTO_BYTES = b"id-photo-fixture-bytes"
FRAME_BYTES = [b"frame-1-bytes", b"frame-2-bytes"]


async def _expected_decision_and_state(nonce: str, include_id_photo: bool) -> tuple[Decision, VerificationState]:
    challenge = Challenge(
        challenge_id="ignored-in-scoring",
        prompt_sequence=["blink"],
        nonce=nonce,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    verification_input = VerificationInput(
        user_ref="user-1",
        selfie=SELFIE_BYTES,
        id_photo=ID_PHOTO_BYTES if include_id_photo else None,
        liveness_frames=FRAME_BYTES,
        challenge=challenge,
    )
    layer_results = [await layer.run(verification_input) for layer in DEFAULT_SYNC_LAYERS]
    _risk_score, decision, _reasons = aggregate(layer_results)
    expected_state = VerificationState.REJECTED if decision == Decision.DENY else VerificationState.PROVISIONAL
    return decision, expected_state


def _post_verify(challenge_id: str, user_ref: str | None = None, include_id_photo: bool = True, include_frames: bool = True):
    files = [("selfie", ("selfie.jpg", SELFIE_BYTES, "image/jpeg"))]
    if include_id_photo:
        files.append(("id_photo", ("id.jpg", ID_PHOTO_BYTES, "image/jpeg")))
    if include_frames:
        for i, frame in enumerate(FRAME_BYTES):
            files.append(("liveness_frames", (f"frame{i}.jpg", frame, "image/jpeg")))

    return client.post(
        "/verify",
        data={"challenge_id": challenge_id, "user_ref": user_ref or f"user-{uuid.uuid4()}"},
        files=files,
    )


@pytest.mark.asyncio
async def test_verify_happy_path_returns_provisional_or_rejected_consistently():
    challenge = client.post("/challenge").json()
    user_ref = f"user-{uuid.uuid4()}"
    response = _post_verify(challenge["challenge_id"], user_ref=user_ref)
    assert response.status_code == 200

    body = response.json()
    expected_decision, expected_state = await _expected_decision_and_state(challenge["nonce"], include_id_photo=True)

    assert body["decision"] == expected_decision.value
    assert body["state"] == expected_state.value
    assert len(body["layers"]) == 4
    assert body["user_ref"] == user_ref


def test_verify_missing_challenge_id_field_is_422():
    response = client.post(
        "/verify",
        data={"user_ref": "user-1"},
        files=[("selfie", ("selfie.jpg", SELFIE_BYTES, "image/jpeg"))],
    )
    assert response.status_code == 422


def test_verify_unknown_challenge_id_is_400():
    response = _post_verify(challenge_id="does-not-exist")
    assert response.status_code == 400


def test_verify_document_job_id_present_when_id_photo_sent():
    challenge = client.post("/challenge").json()
    response = _post_verify(challenge["challenge_id"], include_id_photo=True)
    assert response.json()["document_job_id"] is not None


def test_verify_document_job_id_absent_when_no_id_photo():
    challenge = client.post("/challenge").json()
    response = _post_verify(challenge["challenge_id"], include_id_photo=False)
    assert response.json()["document_job_id"] is None


def test_reusing_a_challenge_raises_the_liveness_risk():
    """Challenges are single-use: the second attempt on one is a replay, and
    must reach the caller as a scored risk signal rather than being silently
    accepted at the same risk as the first.
    """
    challenge = client.post("/challenge").json()

    first = _post_verify(challenge["challenge_id"]).json()
    second = _post_verify(challenge["challenge_id"]).json()

    def liveness_risk(body):
        return next(layer["risk"] for layer in body["layers"] if layer["layer"] == "liveness")

    assert liveness_risk(second) > liveness_risk(first)
    assert liveness_risk(second) == 1.0


@pytest.mark.parametrize("settled", [VerificationState.VERIFIED, VerificationState.REJECTED])
def test_reverifying_a_settled_user_is_409_not_a_crash(settled):
    """VERIFIED and REJECTED are terminal, so /verify has no legal state to move
    to. That must surface as a conflict rather than an unhandled exception.
    """
    from app.main import state_store

    user_ref = f"user-{uuid.uuid4()}"
    if settled is VerificationState.VERIFIED:
        state_store.transition(user_ref, VerificationState.PROVISIONAL)
    state_store.transition(user_ref, settled)

    challenge = client.post("/challenge").json()
    response = _post_verify(challenge["challenge_id"], user_ref=user_ref)

    assert response.status_code == 409
    assert settled.value in response.json()["detail"]
