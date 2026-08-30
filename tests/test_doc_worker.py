import uuid

import pytest

from app.core.contracts import DocumentJobStatus, VerificationState
from app.document_worker import DocumentReviewWorker, run_automated_checks
from app.state.document_job_store import DocumentJobStore
from app.state.store import VerificationStateStore
from tests.test_mrz import ICAO_TD3_SPECIMEN

ID_PHOTO = b"id-photo-bytes"


def build_worker() -> tuple[DocumentReviewWorker, DocumentJobStore, VerificationStateStore]:
    job_store = DocumentJobStore()
    state_store = VerificationStateStore()
    return DocumentReviewWorker(job_store, state_store), job_store, state_store


def provisional_user(state_store: VerificationStateStore) -> str:
    user_ref = f"user-{uuid.uuid4()}"
    state_store.transition(user_ref, VerificationState.PROVISIONAL)
    return user_ref


@pytest.mark.asyncio
async def test_valid_mrz_escalates_to_human_review_rather_than_approving():
    """A passing MRZ is not an approval: only a reviewer can settle VERIFIED."""
    worker, job_store, state_store = build_worker()
    user_ref = provisional_user(state_store)
    job_id = job_store.enqueue(user_ref, ID_PHOTO, mrz_text=ICAO_TD3_SPECIMEN)

    await worker.process_job(job_id)

    job = job_store.get(job_id)
    assert job.status is DocumentJobStatus.AWAITING_REVIEW
    assert job.detail["mrz_parsed"] is True
    assert job.detail["document_format"] == "TD3"
    assert all(job.detail["checks"].values())
    assert state_store.get(user_ref) is VerificationState.PROVISIONAL


@pytest.mark.asyncio
async def test_valid_mrz_finding_does_not_overclaim_authenticity():
    worker, job_store, state_store = build_worker()
    job_id = job_store.enqueue(provisional_user(state_store), ID_PHOTO, mrz_text=ICAO_TD3_SPECIMEN)

    await worker.process_job(job_id)

    finding = job_store.get(job_id).findings[0]
    assert "NOT that the document is genuine" in finding


@pytest.mark.asyncio
async def test_failed_mrz_check_digits_auto_reject_without_a_human():
    """A field contradicting its own check digit is unambiguous, so the worker
    settles it rather than spending reviewer time on it.
    """
    worker, job_store, state_store = build_worker()
    user_ref = provisional_user(state_store)
    lines = ICAO_TD3_SPECIMEN.splitlines()
    tampered = f"{lines[0]}\n{lines[1][:13]}750812{lines[1][19:]}"
    job_id = job_store.enqueue(user_ref, ID_PHOTO, mrz_text=tampered)

    await worker.process_job(job_id)

    job = job_store.get(job_id)
    assert job.status is DocumentJobStatus.REJECTED
    assert "check digits failed" in job.findings[0]
    assert state_store.get(user_ref) is VerificationState.REJECTED


@pytest.mark.asyncio
async def test_unparseable_mrz_is_rejected():
    worker, job_store, state_store = build_worker()
    user_ref = provisional_user(state_store)
    job_id = job_store.enqueue(user_ref, ID_PHOTO, mrz_text="clearly not an mrz")

    await worker.process_job(job_id)

    job = job_store.get(job_id)
    assert job.status is DocumentJobStatus.REJECTED
    assert "could not be parsed" in job.findings[0]
    assert state_store.get(user_ref) is VerificationState.REJECTED


@pytest.mark.asyncio
async def test_no_mrz_and_no_ocr_escalates_and_says_so():
    """Absence of a check must not read as a passing check."""
    worker, job_store, state_store = build_worker()
    job_id = job_store.enqueue(provisional_user(state_store), ID_PHOTO)

    await worker.process_job(job_id)

    job = job_store.get(job_id)
    assert job.status is DocumentJobStatus.AWAITING_REVIEW
    assert job.detail["mrz_checked"] is False
    assert "no OCR backend installed" in job.findings[0]


@pytest.mark.asyncio
async def test_auto_reject_tolerates_an_already_settled_user():
    """The sync tier may have rejected the user first; the job still settles."""
    worker, job_store, state_store = build_worker()
    user_ref = f"user-{uuid.uuid4()}"
    state_store.transition(user_ref, VerificationState.REJECTED)
    job_id = job_store.enqueue(user_ref, ID_PHOTO, mrz_text="clearly not an mrz")

    await worker.process_job(job_id)

    assert job_store.get(job_id).status is DocumentJobStatus.REJECTED
    assert state_store.get(user_ref) is VerificationState.REJECTED


@pytest.mark.asyncio
async def test_processing_a_vanished_job_is_a_no_op():
    worker, _job_store, _state_store = build_worker()
    await worker.process_job("does-not-exist")  # must not raise


@pytest.mark.asyncio
async def test_queued_jobs_are_processed_by_the_background_loop():
    worker, job_store, state_store = build_worker()
    job_id = job_store.enqueue(provisional_user(state_store), ID_PHOTO, mrz_text=ICAO_TD3_SPECIMEN)

    worker.start()
    try:
        await worker.submit(job_id)
        await worker.wait_until_idle()
    finally:
        await worker.stop()

    assert job_store.get(job_id).status is DocumentJobStatus.AWAITING_REVIEW


@pytest.mark.asyncio
async def test_a_failing_job_does_not_kill_the_worker_loop():
    """One bad job must not stop the queue from draining."""
    worker, job_store, state_store = build_worker()
    good_job = job_store.enqueue(provisional_user(state_store), ID_PHOTO, mrz_text=ICAO_TD3_SPECIMEN)

    worker.start()
    try:
        await worker.submit("does-not-exist")
        await worker.submit(good_job)
        await worker.wait_until_idle()
    finally:
        await worker.stop()

    assert job_store.get(good_job).status is DocumentJobStatus.AWAITING_REVIEW


def test_run_automated_checks_is_usable_without_the_worker():
    """The check logic is a plain function, so it can be exercised (and reused)
    without standing up a queue or a state store.
    """
    store = DocumentJobStore()
    job = store.get(store.enqueue("user-1", ID_PHOTO, mrz_text=ICAO_TD3_SPECIMEN))

    status, findings, detail = run_automated_checks(job)

    assert status is DocumentJobStatus.AWAITING_REVIEW
    assert detail["mrz_parsed"] is True
    assert findings
