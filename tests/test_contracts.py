from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.core.contracts import (
    Challenge,
    Decision,
    LayerResult,
    VerificationState,
    VerifyResponse,
)


def test_layer_result_valid():
    result = LayerResult(
        layer="face_match",
        risk=0.2,
        confidence=0.9,
        ok=True,
        reason="similarity above threshold",
        demonstrator=False,
    )
    assert result.risk == 0.2
    assert result.detail == {}


@pytest.mark.parametrize("field,value", [("risk", 1.5), ("risk", -0.1), ("confidence", 2.0), ("confidence", -1.0)])
def test_layer_result_rejects_out_of_range(field, value):
    kwargs = dict(layer="face_match", risk=0.2, confidence=0.9, ok=True, reason="x")
    kwargs[field] = value
    with pytest.raises(ValidationError):
        LayerResult(**kwargs)


def test_challenge_valid():
    challenge = Challenge(
        challenge_id="c-1",
        prompt_sequence=["blink", "turn_left"],
        nonce="abc123",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    assert challenge.prompt_sequence == ["blink", "turn_left"]


def test_verify_response_valid_with_layers():
    layer = LayerResult(layer="liveness", risk=0.1, confidence=0.5, ok=True, reason="ok", demonstrator=True)
    response = VerifyResponse(
        user_ref="user-1",
        state=VerificationState.PROVISIONAL,
        decision=Decision.ALLOW,
        risk_score=0.15,
        reasons=["all layers passed"],
        layers=[layer],
    )
    assert response.state == VerificationState.PROVISIONAL
    assert response.decision == Decision.ALLOW
    assert response.document_job_id is None


def test_verify_response_rejects_out_of_range_risk_score():
    with pytest.raises(ValidationError):
        VerifyResponse(
            user_ref="user-1",
            state=VerificationState.REJECTED,
            decision=Decision.DENY,
            risk_score=1.2,
        )
