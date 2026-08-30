import random

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from app.core.config import default_challenge_settings
from app.core.contracts import Challenge, Decision, ReviewRequest, StatusResponse, VerificationState, VerifyResponse
from app.layers.base import VerificationInput
from app.orchestrator import Orchestrator
from app.state.challenge_store import ChallengeStore
from app.state.document_job_store import DocumentJobStore
from app.state.store import IllegalStateTransition, VerificationStateStore

app = FastAPI(title="Identity Verification Engine (PoC)")

challenge_store = ChallengeStore(ttl_seconds=default_challenge_settings.ttl_seconds)
document_job_store = DocumentJobStore()
state_store = VerificationStateStore()
orchestrator = Orchestrator(state_store)


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
) -> VerifyResponse:
    challenge = challenge_store.get(challenge_id)
    if challenge is None or challenge_store.is_expired(challenge_id):
        raise HTTPException(status_code=400, detail="invalid or expired challenge_id")

    id_photo_bytes = await id_photo.read() if id_photo is not None else None

    verification_input = VerificationInput(
        user_ref=user_ref,
        selfie=await selfie.read(),
        id_photo=id_photo_bytes,
        liveness_frames=[await frame.read() for frame in liveness_frames],
        challenge=challenge,
    )

    response = await orchestrator.run_sync_tier(verification_input)

    if id_photo_bytes is not None:
        response.document_job_id = document_job_store.enqueue(user_ref, id_photo_bytes)

    return response


@app.post("/verify/document/async")
async def enqueue_document_review(
    user_ref: str = Form(...),
    id_photo: UploadFile = File(...),
) -> dict[str, str]:
    job_id = document_job_store.enqueue(user_ref, await id_photo.read())
    return {"job_id": job_id}


@app.get("/status/{user_ref}", response_model=StatusResponse)
def get_status(user_ref: str) -> StatusResponse:
    return StatusResponse(user_ref=user_ref, state=state_store.get(user_ref))


@app.post("/review/{job_id}", response_model=StatusResponse)
def review(job_id: str, body: ReviewRequest) -> StatusResponse:
    job = document_job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="document job not found")

    if body.decision == Decision.STEP_UP:
        raise HTTPException(status_code=400, detail="STEP_UP is not a valid async review outcome")

    next_state = VerificationState.VERIFIED if body.decision == Decision.ALLOW else VerificationState.REJECTED
    try:
        state = state_store.transition(job.user_ref, next_state)
    except IllegalStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    document_job_store.settle(job_id, status=state.value, reviewer_note=body.reviewer_note)

    return StatusResponse(user_ref=job.user_ref, state=state)
