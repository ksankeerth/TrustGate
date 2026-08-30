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


class LayerResult(BaseModel):
    layer: str
    risk: float = Field(ge=0.0, le=1.0, description="0=no risk, 1=max risk")
    confidence: float = Field(ge=0.0, le=1.0)
    ok: bool
    reason: str
    detail: dict[str, Any] = Field(default_factory=dict)
    demonstrator: bool = False


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
