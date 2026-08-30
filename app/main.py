import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from app.core.config import default_challenge_settings
from app.core.contracts import (
    Challenge,
    Decision,
    DocumentJobStatus,
    ReviewRequest,
    StatusResponse,
    VerificationState,
    VerifyResponse,
)
from app.document_worker import DocumentReviewWorker
from app.integrations.thunderid_client import build_thunderid_client
from app.layers.base import VerificationInput
from app.orchestrator import Orchestrator
from app.state.challenge_store import ChallengeStore
from app.state.document_job_store import DocumentJobStore
from app.state.store import IllegalStateTransition, VerificationStateStore

challenge_store = ChallengeStore(ttl_seconds=default_challenge_settings.ttl_seconds)
document_job_store = DocumentJobStore()
state_store = VerificationStateStore()
orchestrator = Orchestrator(state_store)
thunderid_client = build_thunderid_client()
document_worker = DocumentReviewWorker(document_job_store, state_store, thunderid_client)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    document_worker.start()
    try:
        yield
    finally:
        await document_worker.stop()


app = FastAPI(title="Identity Verification Engine (PoC)", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/challenge", response_model=Challenge)
def create_challenge() -> Challenge:
    prompt_sequence = random.sample(
        default_challenge_settings.prompt_pool,
        k=default_challenge_settings.sequence_length,
    )
    return challenge_store.issue(prompt_sequence)


@app.post("/verify", response_model=VerifyResponse)
async def verify(
    challenge_id: str = Form(...),
    user_ref: str = Form(...),
    selfie: UploadFile = File(...),
    id_photo: UploadFile | None = File(None),
    liveness_frames: list[UploadFile] = File(default=[]),
    frame_binding: str | None = Form(None),
    mrz_text: str | None = Form(None),
) -> VerifyResponse:
    challenge = challenge_store.get(challenge_id)
    if challenge is None or challenge_store.is_expired(challenge_id):
        raise HTTPException(status_code=400, detail="invalid or expired challenge_id")

    # Consumed rather than rejected outright so a replayed challenge reaches
    # the liveness layer as a risk signal, alongside whatever else that attempt
    # looks like, instead of collapsing into an opaque 4xx.
    challenge_first_use = challenge_store.consume(challenge_id)

    id_photo_bytes = await id_photo.read() if id_photo is not None else None

    verification_input = VerificationInput(
        user_ref=user_ref,
        selfie=await selfie.read(),
        id_photo=id_photo_bytes,
        liveness_frames=[await frame.read() for frame in liveness_frames],
        challenge=challenge,
        metadata={"challenge_first_use": challenge_first_use, "frame_binding": frame_binding},
    )

    response = await orchestrator.run_sync_tier(verification_input)

    if id_photo_bytes is not None:
        # Queued, not awaited: the document tier runs out of band so the user
        # can hold provisional access while it is still being checked.
        response.document_job_id = document_job_store.enqueue(user_ref, id_photo_bytes, mrz_text)
        await document_worker.submit(response.document_job_id)

    return response


@app.post("/verify/document/async")
async def enqueue_document_review(
    user_ref: str = Form(...),
    id_photo: UploadFile = File(...),
    mrz_text: str | None = Form(None),
) -> dict[str, str]:
    job_id = document_job_store.enqueue(user_ref, await id_photo.read(), mrz_text)
    await document_worker.submit(job_id)
    return {"job_id": job_id}


@app.get("/document/{job_id}")
def get_document_job(job_id: str) -> dict:
    job = document_job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="document job not found")
    return {
        "job_id": job.job_id,
        "user_ref": job.user_ref,
        "status": job.status.value,
        "findings": job.findings,
        "detail": job.detail,
        "reviewer_note": job.reviewer_note,
    }


@app.get("/status/{user_ref}", response_model=StatusResponse)
def get_status(user_ref: str) -> StatusResponse:
    return StatusResponse(user_ref=user_ref, state=state_store.get(user_ref))


@app.post("/review/{job_id}", response_model=StatusResponse)
async def review(job_id: str, body: ReviewRequest) -> StatusResponse:
    job = document_job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="document job not found")

    if body.decision == Decision.STEP_UP:
        raise HTTPException(status_code=400, detail="STEP_UP is not a valid async review outcome")

    if job.status in (DocumentJobStatus.VERIFIED, DocumentJobStatus.REJECTED):
        raise HTTPException(status_code=409, detail=f"document job already settled as {job.status.value}")

    next_state = VerificationState.VERIFIED if body.decision == Decision.ALLOW else VerificationState.REJECTED
    try:
        state = state_store.transition(job.user_ref, next_state)
    except IllegalStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job_status = DocumentJobStatus.VERIFIED if body.decision == Decision.ALLOW else DocumentJobStatus.REJECTED
    document_job_store.settle(job_id, status=job_status, reviewer_note=body.reviewer_note)

    # Out-of-band propagation: no login flow is running by now, so this is how
    # the identity product learns the review outcome. Deliberately not fatal --
    # the local store is the system of record, and the review has already been
    # recorded, so an unreachable ThunderID must not fail the reviewer's action.
    await thunderid_client.update_verification_status(job.user_ref, state)

    return StatusResponse(user_ref=job.user_ref, state=state)
