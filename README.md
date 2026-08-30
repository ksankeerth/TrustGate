# TrustGate

An external identity-verification service (Python / FastAPI) built for an
adaptive-auth product's onboarding/login flow. It runs several verification
layers, aggregates them into a single risk decision, and exposes an
asynchronous document-review tier that settles full verification later.

## Current status

The full **challenge → verify → status → review** flow works end to end.
Two of the four sync-tier layers (liveness, injection) are still
**deterministic stubs** -- hash-derived mock scores, not real models -- so
decisions from those will vary run to run rather than reflecting real
biometric signal. Real models replace these stubs incrementally; every layer
result already carries a `demonstrator` flag so callers can tell mock layers
from production-grade ones.

**Deepfake detection is real**, but disabled by default (`DeepfakeSettings.enabled
= False` in `app/core/config.py`) so the default test suite and app startup stay
fast and network-free. It supports two interchangeable open-source checkpoints
(both Apache-2.0, ungated):

| `model_choice` | Checkpoint | Base |
|---|---|---|
| `vit` (default) | `prithivMLmods/Deep-Fake-Detector-v2-Model` | `google/vit-base-patch16-224-in21k` |
| `siglip2` | `prithivMLmods/Deepfake-Detect-Siglip2` | `google/siglip2-base-patch16-224` |

Each checkpoint's native label order is normalized inside the layer to a
canonical `fake_probability` -> `risk`, so the aggregator never needs to know
which checkpoint is active. Model weights cache to `.cache/huggingface/`
(project-local, gitignored) rather than the default `~/.cache/huggingface`.

To enable it, set `DeepfakeSettings.enabled = True` (and optionally
`model_choice`, `device`) before the app builds its default layer list.
**As with any deepfake classifier, treat its output as a risk signal, not a
verdict** -- it does not generalize to unseen generators.

Its dedicated test (`tests/test_deepfake_real.py`, marked `slow`, excluded
from the default `pytest` run) needs two local images it does not ship with
the repo -- `samples/deepfake_eval/known_real.jpg` and `known_fake.jpg`
(gitignored) -- since real face photos and deepfake samples shouldn't be
committed to a public repo. Add your own to run it: `pytest -m slow`.

**Face match is also real**, same pattern: disabled by default
(`FaceMatchSettings.enabled = False`). It uses MTCNN (face
detection/crop/alignment) and `InceptionResnetV1` (pretrained on `vggface2`
by default, or `casia-webface`) for embeddings, and cosine similarity
between the selfie and ID-photo embeddings maps to risk (low similarity =
high risk, threshold configurable). Weights cache to `.cache/torch/`
(project-local, via `TORCH_HOME`). If no face is detected in either image,
the layer reports maximum risk rather than erroring.

Its dedicated test (`tests/test_face_match_real.py`, also `slow`) needs three
local images -- `samples/face_match_eval/same_person_a.jpg`, `same_person_b.jpg`,
and `different_person.jpg` (gitignored) -- for the same reason as above.

The MTCNN/InceptionResnetV1 code itself is vendored from `facenet-pytorch`
(MIT licensed) into `app/layers/_vendor/facenet_pytorch/` rather than taken
as an ordinary dependency -- its latest PyPI release pins `torch<2.3.0` /
`numpy<2.0.0` / `Pillow<10.3.0`, versions with no prebuilt wheels for recent
Python, which would force a broken from-source build (or downgrade the
torch/numpy the deepfake layer needs) if installed normally. See
`app/layers/_vendor/facenet_pytorch/README.md` for the details and exactly
what was changed from upstream (nothing behavioral).

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
