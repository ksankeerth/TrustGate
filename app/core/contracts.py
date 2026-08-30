from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class VerificationState(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    PROVISIONAL = "PROVISIONAL"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class Decision(str, Enum):
    ALLOW = "ALLOW"
    STEP_UP = "STEP_UP"
    DENY = "DENY"


class FailPosture(str, Enum):
    """What a layer's failure means for the overall decision.

    FAIL_CLOSED — an unusable layer counts as maximum risk. A verification
    check that did not run has not passed, and an attacker who can induce
    errors must not thereby weaken the decision.

    FAIL_OPEN — an unusable layer is excluded from scoring entirely (zero
    weight), leaving the remaining layers to decide. Favours availability, at
    the cost of making error-induction a viable way to shed a check.
    """

    FAIL_CLOSED = "FAIL_CLOSED"
    FAIL_OPEN = "FAIL_OPEN"


class DocumentJobStatus(str, Enum):
    PENDING = "PENDING"  # queued, automated checks not yet run
    AWAITING_REVIEW = "AWAITING_REVIEW"  # automated checks done, needs a human decision
    REJECTED = "REJECTED"  # settled: failed automated checks, or a reviewer denied it
    VERIFIED = "VERIFIED"  # settled: a reviewer approved it


class LayerResult(BaseModel):
    layer: str
    risk: float = Field(ge=0.0, le=1.0, description="0=no risk, 1=max risk")
    confidence: float = Field(ge=0.0, le=1.0)
    ok: bool
    reason: str
    detail: dict[str, Any] = Field(default_factory=dict)
    demonstrator: bool = False
    # Wall-clock time for this layer, filled in by the orchestrator that
    # scheduled it -- a layer does not time itself.
    duration_ms: float | None = None


class Challenge(BaseModel):
    challenge_id: str
    prompt_sequence: list[str]
    nonce: str
    expires_at: datetime


class VerifyResponse(BaseModel):
    user_ref: str
    state: VerificationState
    decision: Decision
    risk_score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    layers: list[LayerResult] = Field(default_factory=list)
    document_job_id: str | None = None
    # Wall-clock time for the whole sync tier. Because the layers run
    # concurrently this is close to the slowest layer, not the sum of them.
    total_duration_ms: float | None = None


class StatusResponse(BaseModel):
    user_ref: str
    state: VerificationState


class ReviewRequest(BaseModel):
    decision: Decision
    reviewer_note: str | None = None
