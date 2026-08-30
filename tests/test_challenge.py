from fastapi.testclient import TestClient

from app.core.config import default_challenge_settings
from app.main import app
from app.state.challenge_store import ChallengeStore

client = TestClient(app)


def test_challenge_endpoint_response_schema():
    response = client.post("/challenge")
    assert response.status_code == 200

    body = response.json()
    assert set(body.keys()) == {"challenge_id", "prompt_sequence", "nonce", "expires_at"}
    assert isinstance(body["challenge_id"], str) and body["challenge_id"]
    assert isinstance(body["nonce"], str) and body["nonce"]
    assert isinstance(body["prompt_sequence"], list)
    assert len(body["prompt_sequence"]) == default_challenge_settings.sequence_length
    assert all(prompt in default_challenge_settings.prompt_pool for prompt in body["prompt_sequence"])


def test_two_challenges_have_different_ids_and_nonces():
    first = client.post("/challenge").json()
    second = client.post("/challenge").json()

    assert first["challenge_id"] != second["challenge_id"]
    assert first["nonce"] != second["nonce"]


def test_challenge_store_not_expired_immediately():
    store = ChallengeStore(ttl_seconds=60)
    challenge = store.issue(["blink"])
    assert store.is_expired(challenge.challenge_id) is False


def test_challenge_store_honors_expiry():
    store = ChallengeStore(ttl_seconds=-1)
    challenge = store.issue(["blink"])
    assert store.is_expired(challenge.challenge_id) is True


def test_challenge_store_unknown_id_is_expired():
    store = ChallengeStore(ttl_seconds=60)
    assert store.is_expired("does-not-exist") is True
    assert store.get("does-not-exist") is None
