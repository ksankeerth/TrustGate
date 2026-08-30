import random

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from app.core.config import default_challenge_settings
from app.core.contracts import Challenge, VerifyResponse
from app.layers.base import VerificationInput
from app.orchestrator import Orchestrator
from app.state.challenge_store import ChallengeStore
from app.state.document_job_store import DocumentJobStore
from app.state.store import VerificationStateStore

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
