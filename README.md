# TrustGate

An external identity-verification service (Python / FastAPI) built for an
adaptive-auth product's onboarding/login flow. It runs several verification
layers, aggregates them into a single risk decision, and exposes an
asynchronous document-review tier that settles full verification later.

## Current status

The full **challenge → verify → status → review** flow works end to end, and
all four sync-tier layers are now implemented:

| Layer | Status | Default |
|---|---|---|
| Face match | Real (MTCNN + InceptionResnetV1) | Off -- mock stub runs instead |
| Deepfake | Real (SigLIP2 / ViT classifier) | Off -- mock stub runs instead |
| Liveness | Real, **demonstrator** | On |
| Injection | Real, **demonstrator** | On |

The two model-backed layers are off by default so the test suite and app
startup stay fast and download-free; enable them explicitly (see below). The
two demonstrator layers need no model and run by default, but are deliberately
weak -- see their limitations sections. Every layer result carries a
`demonstrator` flag so callers can tell weak or mock layers from
production-grade ones, and the aggregator down-weights both.

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

## Liveness: a demonstrator, and its limitations

The liveness layer needs no model download, so unlike the two above it runs
by default. It is deliberately **weak by design and self-reporting**: every
result it emits carries `demonstrator: true` and a `reason` that spells out
what it did and did not establish. Read its output as "nothing obviously
wrong", never as "a live human was present".

**What it actually checks** (all genuinely enforced server-side):

| Check | Catches |
|---|---|
| Challenge exists and is unexpired | Attempts not tied to a fresh, server-issued challenge |
| Challenge is single-use | Re-presenting an earlier attempt's `challenge_id` |
| Frame binding (optional HMAC) | Wholesale replay of a previously captured payload |
| Frame count | Payloads too thin to assess at all |
| Inter-frame variation | One still image submitted as if it were a capture |

**Frame binding** is an HMAC the client computes over its frames keyed by the
challenge nonce (`compute_frame_binding` in `app/layers/liveness.py`), sent as
the optional `frame_binding` form field on `/verify`. It proves the payload
was assembled by something holding *this* challenge's nonce. It does **not**
prove the frames were captured live: the nonce goes to the client in the
clear, so anyone who can request a challenge can compute a valid binding over
pre-recorded footage. Omitting the field is scored as a real gap, not ignored.

**What it does NOT do — the honest part:**

- **It does not verify the prompted actions were performed.** The challenge
  asks for e.g. "blink, nod, open_mouth", and nothing checks that any of that
  happened. Doing so needs real action recognition, which is out of scope here.
- **It is not a certified presentation-attack detection control.** No iBeta or
  equivalent testing has been done, and none is claimed.
- **Its motion check is crude.** A mean per-pixel delta between consecutive
  frames distinguishes a repeated still from *something* changing; it cannot
  distinguish a live face from a video of a face, a mask, or a screen replay.
- **A clean pass never scores zero risk.** Passing every check still returns
  `baseline_risk` (0.2 by default), because a clean run from this layer is
  weak evidence. Its `confidence` is likewise capped low (0.35), which the
  aggregator uses to down-weight it against real layers.
- **Defeating an attacker who holds a valid nonce is out of its reach.** That
  is nominally the injection layer's territory -- and, as the next section
  explains, largely beyond its reach too.

## Injection detection: the weakest layer here

**Injection** means bypassing the camera altogether -- a virtual camera,
emulator, or feeding frames straight to the API -- as distinct from a
*presentation* attack held up to a real lens. It also runs by default and needs
no model.

Be clear-eyed about this one: **detecting injection from uploaded stills is
close to a lost cause.** Every signal available server-side is either trivially
forgeable or routinely absent on genuine traffic. Production systems address
injection with **client attestation** (Play Integrity, App Attest, hardware-backed
key attestation) proving the frames came from a real camera on an untampered
device. This service receives an HTTP upload and cannot attest anything about
its origin. What follows is a best-effort demonstrator, scored to be leaned on
least: the **lowest confidence of any layer (0.2)** and the **highest baseline
risk (0.3)**.

**What it looks at:**

| Signal | Rationale | Why it's weak |
|---|---|---|
| EXIF `Software` tag vs. known encoder/virtual-camera markers | Injection tooling often leaves a trace | Trivially stripped or spoofed |
| Missing camera make/model | Synthetic frames often carry no camera metadata | Many legitimate clients strip EXIF for privacy |
| Missing capture timestamp | Incomplete provenance | Same as above |
| Identical frame byte lengths | Independently encoded camera frames rarely match exactly | Easy to vary deliberately |
| Sensor-noise floor | Real sensors are never perfectly smooth; renders often are | A photo of a blank wall scores low; noise is easy to add |
| Frame dimension disagreement | One capture session should be internally consistent | Easy to normalise |

**What it does NOT do:**

- **It cannot establish that frames came from a real camera.** Only attestation
  can, and that is a client-side capability this service does not have.
- **Expect modest accuracy in both directions.** A privacy-conscious client that
  strips EXIF looks suspicious; a careful attacker who forges plausible metadata
  and adds noise looks clean. Do not tune a hard gate on this layer's output.
- **A clean pass never scores zero risk**, for the same reason as liveness: at
  `baseline_risk` 0.3, absence of evidence is not evidence of absence.
- **No certification is claimed.**

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
