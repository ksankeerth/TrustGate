import secrets
import uuid
from datetime import datetime, timedelta, timezone

from app.core.contracts import Challenge


class ChallengeStore:
    """In-memory, TTL-bound store for issued liveness challenges."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._challenges: dict[str, Challenge] = {}
        self._consumed: set[str] = set()

    def issue(self, prompt_sequence: list[str]) -> Challenge:
        challenge = Challenge(
            challenge_id=str(uuid.uuid4()),
            prompt_sequence=prompt_sequence,
            nonce=secrets.token_hex(16),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._ttl_seconds),
        )
        self._challenges[challenge.challenge_id] = challenge
        return challenge

    def get(self, challenge_id: str) -> Challenge | None:
        return self._challenges.get(challenge_id)

    def is_expired(self, challenge_id: str) -> bool:
        challenge = self.get(challenge_id)
        if challenge is None:
            return True
        return datetime.now(timezone.utc) >= challenge.expires_at

    def consume(self, challenge_id: str) -> bool:
        """Mark a challenge as used; return True if this was its first use.

        Challenges are single-use: presenting one a second time means the
        client is replaying an earlier attempt, which is the one replay signal
        the server can establish on its own.
        """
        if challenge_id in self._consumed:
            return False
        self._consumed.add(challenge_id)
        return True
