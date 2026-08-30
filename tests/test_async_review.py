import uuid

from fastapi.testclient import TestClient

from app.core.contracts import VerificationState
from app.main import app, state_store

client = TestClient(app)


def _enqueue_document_job(user_ref: str) -> str:
    response = client.post(
        "/verify/document/async",
        data={"user_ref": user_ref},
        files=[("id_photo", ("id.jpg", b"id-photo-bytes", "image/jpeg"))],
    )
    assert response.status_code == 200
    return response.json()["job_id"]


def test_full_async_review_flow_allow_settles_verified():
    user_ref = f"user-{uuid.uuid4()}"
    state_store.transition(user_ref, VerificationState.PROVISIONAL)

    status = client.get(f"/status/{user_ref}").json()
    assert status["state"] == VerificationState.PROVISIONAL.value

    job_id = _enqueue_document_job(user_ref)

    review = client.post(f"/review/{job_id}", json={"decision": "ALLOW", "reviewer_note": "looks genuine"})
    assert review.status_code == 200
    assert review.json()["state"] == VerificationState.VERIFIED.value

    status = client.get(f"/status/{user_ref}").json()
    assert status["state"] == VerificationState.VERIFIED.value


def test_full_async_review_flow_deny_settles_rejected():
    user_ref = f"user-{uuid.uuid4()}"
    state_store.transition(user_ref, VerificationState.PROVISIONAL)

    job_id = _enqueue_document_job(user_ref)

    review = client.post(f"/review/{job_id}", json={"decision": "DENY", "reviewer_note": "forged security features"})
    assert review.status_code == 200
    assert review.json()["state"] == VerificationState.REJECTED.value

    status = client.get(f"/status/{user_ref}").json()
    assert status["state"] == VerificationState.REJECTED.value


def test_status_for_unknown_user_is_unverified():
    status = client.get(f"/status/user-{uuid.uuid4()}").json()
    assert status["state"] == VerificationState.UNVERIFIED.value


def test_review_unknown_job_id_is_404():
    response = client.post("/review/does-not-exist", json={"decision": "ALLOW"})
    assert response.status_code == 404


def test_review_for_user_still_unverified_is_409():
    # No prior /verify call put this user into PROVISIONAL, so ALLOW would
    # require an illegal UNVERIFIED -> VERIFIED jump; must surface as a
    # proper 409, not an unhandled exception.
    user_ref = f"user-{uuid.uuid4()}"
    job_id = _enqueue_document_job(user_ref)

    response = client.post(f"/review/{job_id}", json={"decision": "ALLOW"})
    assert response.status_code == 409


def test_review_step_up_decision_is_400():
    user_ref = f"user-{uuid.uuid4()}"
    state_store.transition(user_ref, VerificationState.PROVISIONAL)
    job_id = _enqueue_document_job(user_ref)

    response = client.post(f"/review/{job_id}", json={"decision": "STEP_UP"})
    assert response.status_code == 400
