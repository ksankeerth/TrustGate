# TrustGate

An external identity-verification service (Python / FastAPI) built for an
adaptive-auth product's onboarding/login flow. It runs several verification
layers, aggregates them into a single risk decision, and exposes an
asynchronous document-review tier that settles full verification later.

## Current status

The full **challenge → verify → status → review** flow works end to end.
The four sync-tier layers (face match, liveness, deepfake, injection) are
currently **deterministic stubs** -- hash-derived mock scores, not real
models -- so decisions will vary run to run rather than reflecting real
biometric signal. Real models replace these stubs incrementally; every layer
result already carries a `demonstrator` flag so callers can tell mock layers
from production-grade ones once real layers land.

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

## Demo script

Exercises the full flow against a running server:

```bash
./scripts/demo.sh
```

Set `BASE_URL` to point at a non-default host/port, and `USER_REF` to pin a
specific user identifier (otherwise one is generated per run).

## API reference

| Endpoint | Purpose |
|---|---|
| `POST /challenge` | Issues `{challenge_id, prompt_sequence, nonce, expires_at}` for the liveness anti-replay handshake. |
| `POST /verify` | Runs the sync tier (concurrently) on a selfie + optional ID photo + optional liveness frames; returns a flat `VerifyResponse`. Enqueues an async document-review job when `id_photo` is present. |
| `POST /verify/document/async` | Enqueues a document-review job directly (outside the sync flow); returns `{job_id}`. |
| `GET /status/{user_ref}` | Returns the user's current verification state. |
| `POST /review/{job_id}` | Human-in-the-loop settle: `{decision, reviewer_note}` -> transitions `PROVISIONAL` to `VERIFIED` (ALLOW) or `REJECTED` (DENY). |

### Verification state machine

```
UNVERIFIED --(sync tier passes)--> PROVISIONAL --(async review passes)--> VERIFIED
UNVERIFIED --(sync tier fails)---------------------------------------> REJECTED
                                   PROVISIONAL --(async review fails)-> REJECTED
```

`VERIFIED` and `REJECTED` are terminal. `PROVISIONAL -> PROVISIONAL` is
allowed (idempotent) so a retried `/verify` call doesn't fail.

## Integration contract (adaptive-auth product)

The service is designed to be called by an identity product's flow engine
via two different integration points.

### 1. Inline (synchronous) -- generic HTTP-request executor

A flow's HTTP-request step calls `POST /verify` and branches on the
response. `decision` and `risk_score` are deliberately **top-level, flat
JSON fields** so they map directly onto a simple field-path response
mapping, with no need to reach into nested objects:

```json
{
  "user_ref": "demo-user",
  "state": "PROVISIONAL",
  "decision": "ALLOW",
  "risk_score": 0.28,
  "reasons": ["injection: risk=0.56"],
  "layers": [ "...per-layer detail..." ],
  "document_job_id": "88632a8e-4dbe-49c4-9895-5efbd5b4fa19"
}
```

Response-mapping shape (pseudocode; adapt to the specific flow engine's
syntax):

```
responseMapping:
  decision   -> $.decision
  risk_score -> $.risk_score

branch on decision:
  ALLOW   -> continue flow
  STEP_UP -> step-up executor (OTP / passkey)
  DENY    -> fail path
```

The call must respect the flow engine's `failOnError` semantics -- decide
explicitly whether a service error should fail the flow open or closed
(a config-level fail posture is not yet implemented; see the layers'
`demonstrator` scores as a proxy for confidence in the interim).

### 2. Out-of-band (asynchronous) -- user-attribute update

When the async document review settles (via `POST /review/{job_id}`), the
service should push the result to the identity product as a custom
`verification_status` user attribute (`UNVERIFIED | PROVISIONAL | VERIFIED |
REJECTED`), updated **after the login/onboarding flow has already ended** --
this is what makes the async tier possible without holding a flow open.

```
async settle (this service)
  -> call identity product's user-management API
       PATCH /users/{id}  { "attributes": { "verification_status": "VERIFIED" } }
  -> downstream app reacts (unlock full access / notify user)
```

The exact endpoint path and auth scheme (client-credentials token vs. an
admin API key) for the target identity product are not yet wired up --
that integration is a later milestone, gated on confirming the specifics
from that product's own API reference. The adapter boundary (a thin,
mockable client) is where that will land; nothing in this service's own
state machine depends on it.

**Session/token caveat:** flipping `verification_status` out-of-band does
not retroactively change a token already issued to the user. Either the
user picks up the new status on their next login/token refresh, or any
downstream app gating sensitive actions must read `verification_status`
live rather than trusting a cached claim from an older token.
