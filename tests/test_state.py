import pytest

from app.core.contracts import VerificationState
from app.state.store import IllegalStateTransition, VerificationStateStore


def test_new_user_starts_unverified():
    store = VerificationStateStore()
    assert store.get("user-1") == VerificationState.UNVERIFIED


def test_legal_path_unverified_to_provisional_to_verified():
    store = VerificationStateStore()
    store.transition("user-1", VerificationState.PROVISIONAL)
    assert store.get("user-1") == VerificationState.PROVISIONAL

    store.transition("user-1", VerificationState.VERIFIED)
    assert store.get("user-1") == VerificationState.VERIFIED


def test_rejected_reachable_from_unverified():
    store = VerificationStateStore()
    store.transition("user-1", VerificationState.REJECTED)
    assert store.get("user-1") == VerificationState.REJECTED


def test_rejected_reachable_from_provisional():
    store = VerificationStateStore()
    store.transition("user-1", VerificationState.PROVISIONAL)
    store.transition("user-1", VerificationState.REJECTED)
    assert store.get("user-1") == VerificationState.REJECTED


def test_provisional_to_provisional_is_idempotent():
    # A user can be re-run through the sync tier (retry, re-attempt) and land
    # back in PROVISIONAL without that being treated as an illegal jump.
    store = VerificationStateStore()
    store.transition("user-1", VerificationState.PROVISIONAL)
    store.transition("user-1", VerificationState.PROVISIONAL)
    assert store.get("user-1") == VerificationState.PROVISIONAL


def test_illegal_jump_unverified_to_verified_raises():
    store = VerificationStateStore()
    with pytest.raises(IllegalStateTransition):
        store.transition("user-1", VerificationState.VERIFIED)


@pytest.mark.parametrize(
    "terminal_state",
    [VerificationState.VERIFIED, VerificationState.REJECTED],
)
@pytest.mark.parametrize(
    "attempted_next",
    [VerificationState.UNVERIFIED, VerificationState.PROVISIONAL, VerificationState.VERIFIED, VerificationState.REJECTED],
)
def test_terminal_states_reject_all_further_transitions(terminal_state, attempted_next):
    store = VerificationStateStore()
    if terminal_state == VerificationState.VERIFIED:
        store.transition("user-1", VerificationState.PROVISIONAL)
    store.transition("user-1", terminal_state)

    with pytest.raises(IllegalStateTransition):
        store.transition("user-1", attempted_next)


def test_transitions_are_isolated_per_user():
    store = VerificationStateStore()
    store.transition("user-1", VerificationState.PROVISIONAL)
    assert store.get("user-2") == VerificationState.UNVERIFIED
