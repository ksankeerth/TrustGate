from app.core.contracts import VerificationState

_ALLOWED_TRANSITIONS: dict[VerificationState, set[VerificationState]] = {
    VerificationState.UNVERIFIED: {VerificationState.PROVISIONAL, VerificationState.REJECTED},
    VerificationState.PROVISIONAL: {VerificationState.PROVISIONAL, VerificationState.VERIFIED, VerificationState.REJECTED},
    VerificationState.VERIFIED: set(),
    VerificationState.REJECTED: set(),
}


class IllegalStateTransition(Exception):
    pass


class VerificationStateStore:
    """In-memory verification state machine, keyed by user_ref.

    Every user starts UNVERIFIED implicitly (no row needed until the first
    transition). Swappable for a persistent backend later without changing
    the transition rules above.
    """

    def __init__(self) -> None:
        self._states: dict[str, VerificationState] = {}

    def get(self, user_ref: str) -> VerificationState:
        return self._states.get(user_ref, VerificationState.UNVERIFIED)

    def is_settled(self, user_ref: str) -> bool:
        """True once the user has reached a terminal state.

        Lets callers refuse work up front rather than discovering the illegal
        transition only after the expensive part has already run.
        """
        return not _ALLOWED_TRANSITIONS[self.get(user_ref)]

    def transition(self, user_ref: str, to_state: VerificationState) -> VerificationState:
        current = self.get(user_ref)
        if to_state not in _ALLOWED_TRANSITIONS[current]:
            raise IllegalStateTransition(f"cannot transition user '{user_ref}' from {current} to {to_state}")
        self._states[user_ref] = to_state
        return to_state
