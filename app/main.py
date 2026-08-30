import random

from fastapi import FastAPI

from app.core.config import default_challenge_settings
from app.core.contracts import Challenge
from app.state.challenge_store import ChallengeStore

app = FastAPI(title="Identity Verification Engine (PoC)")

challenge_store = ChallengeStore(ttl_seconds=default_challenge_settings.ttl_seconds)


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
