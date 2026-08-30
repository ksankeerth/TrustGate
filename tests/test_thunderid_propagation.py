import uuid

import pytest

from app.core.contracts import VerificationState
from app.document_worker import DocumentReviewWorker
from app.state.document_job_store import DocumentJobStore
from app.state.store import VerificationStateStore
from tests.test_mrz import ICAO_TD3_SPECIMEN


class RecordingThunderIdClient:
    def __init__(self, result: bool = True) -> None:
        self.calls: list[tuple[str, VerificationState]] = []
        self._result = result

    async def update_verification_status(self, user_ref, state):
        self.calls.append((user_ref, state))
        return self._result


class ExplodingThunderIdClient:
    async def update_verification_status(self, user_ref, state):
        raise RuntimeError("ThunderID unreachable")


def provisional_user(state_store: VerificationStateStore) -> str:
    user_ref = f"user-{uuid.uuid4()}"
    state_store.transition(user_ref, VerificationState.PROVISIONAL)
    return user_ref


@pytest.mark.asyncio
async def test_automated_rejection_is_propagated():
    job_store, state_store = DocumentJobStore(), VerificationStateStore()
    client = RecordingThunderIdClient()
    worker = DocumentReviewWorker(job_store, state_store, client)

    user_ref = provisional_user(state_store)
    job_id = job_store.enqueue(user_ref, b"id", mrz_text="not a valid mrz")
    await worker.process_job(job_id)

    assert client.calls == [(user_ref, VerificationState.REJECTED)]


@pytest.mark.asyncio
async def test_escalation_to_human_review_is_not_propagated():
    """AWAITING_REVIEW is not a settled outcome, so nothing should be pushed."""
    job_store, state_store = DocumentJobStore(), VerificationStateStore()
    client = RecordingThunderIdClient()
    worker = DocumentReviewWorker(job_store, state_store, client)

    job_id = job_store.enqueue(provisional_user(state_store), b"id", mrz_text=ICAO_TD3_SPECIMEN)
    await worker.process_job(job_id)

    assert client.calls == []


@pytest.mark.asyncio
async def test_worker_defaults_to_not_propagating_at_all():
    """Constructed without a client, the worker must not blow up or invent one."""
    job_store, state_store = DocumentJobStore(), VerificationStateStore()
    worker = DocumentReviewWorker(job_store, state_store)

    job_id = job_store.enqueue(provisional_user(state_store), b"id", mrz_text="not a valid mrz")
    await worker.process_job(job_id)  # must not raise

    assert job_store.get(job_id).status.value == "REJECTED"


@pytest.mark.asyncio
async def test_local_state_still_settles_when_propagation_fails():
    """The local store is the system of record: an unreachable ThunderID must
    not undo or block a settled verification.
    """
    job_store, state_store = DocumentJobStore(), VerificationStateStore()
    worker = DocumentReviewWorker(job_store, state_store, RecordingThunderIdClient(result=False))

    user_ref = provisional_user(state_store)
    job_id = job_store.enqueue(user_ref, b"id", mrz_text="not a valid mrz")
    await worker.process_job(job_id)

    assert state_store.get(user_ref) is VerificationState.REJECTED
    assert job_store.get(job_id).status.value == "REJECTED"


@pytest.mark.asyncio
async def test_queue_survives_a_client_that_raises():
    """A propagation bug must not take down the worker loop."""
    job_store, state_store = DocumentJobStore(), VerificationStateStore()
    worker = DocumentReviewWorker(job_store, state_store, ExplodingThunderIdClient())

    good_ref = provisional_user(state_store)
    bad_job = job_store.enqueue(provisional_user(state_store), b"id", mrz_text="not a valid mrz")
    good_job = job_store.enqueue(good_ref, b"id", mrz_text=ICAO_TD3_SPECIMEN)

    worker.start()
    try:
        await worker.submit(bad_job)
        await worker.submit(good_job)
        await worker.wait_until_idle()
    finally:
        await worker.stop()

    assert job_store.get(good_job).status.value == "AWAITING_REVIEW"


@pytest.mark.asyncio
async def test_human_review_outcome_is_propagated_through_the_api():
    """The reviewer's decision is the other settle path, and must push too."""
    from fastapi.testclient import TestClient

    import app.main as main

    client = RecordingThunderIdClient()
    original = main.thunderid_client
    main.thunderid_client = client
    try:
        api = TestClient(main.app)
        user_ref = f"user-{uuid.uuid4()}"
        main.state_store.transition(user_ref, VerificationState.PROVISIONAL)

        job_id = api.post(
            "/verify/document/async",
            data={"user_ref": user_ref},
            files=[("id_photo", ("id.jpg", b"bytes", "image/jpeg"))],
        ).json()["job_id"]

        response = api.post(f"/review/{job_id}", json={"decision": "ALLOW"})
        assert response.status_code == 200
    finally:
        main.thunderid_client = original

    assert client.calls == [(user_ref, VerificationState.VERIFIED)]
