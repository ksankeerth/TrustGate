# TrustGate

External identity-verification service (Python / FastAPI) for an adaptive-auth
onboarding/login flow. It runs several verification layers concurrently,
aggregates them into one risk decision, and settles full verification later via
an asynchronous document-review tier.

- **Sync tier** — answers inline, while the login flow waits
- **Async tier** — settles out of band, after the flow has ended
- **Two integration points** — a flat JSON risk decision inline; a user-attribute
  write out of band

---

## Architecture at a glance

```text
   CLIENT                IDENTITY PRODUCT            TRUSTGATE
   (app / browser)       (ThunderID :8090)           (FastAPI :8000)
        |                       |                          |
   1    |--- start login ------>|                          |
        |                       |                          |
   2    |--- GET /challenge ---------------------------->  |
        |  <---------------- nonce + prompt sequence ----  |
        |                       |                          |
   3    |  capture selfie / ID / frames, bind to nonce     |
        |                       |                          |
   4    |--- submit ----------> |--- POST /verify ------>  |
        |                       |                          |
        |                       |                     sync tier
        |                       |                     4 layers
        |                       |                          |
        |                       |  <-- decision, risk ---  |
   5    |  <-- ALLOW / STEP_UP / DENY --                   |
        |                       |                          |
        |                       |                   queue doc job
        |                       |                          |
        =========  login flow ENDS, user is PROVISIONAL  =========
        |                       |                          |
        |                       |                     async tier
        |                       |                    MRZ + human
        |                       |                          |
   6    |                       |  <-- PUT /users/{id} --  |
        |                       |      verification_status |
        |                       |                          |
   7    |  <-- full access ---- |                          |
```

**The key idea:** step 5 ends the flow. Steps 6–7 happen with *no flow running* —
which is exactly what makes a slow, human-in-the-loop document check possible
without holding a login open.

---

## Sync tier pipeline

```text
  POST /verify
    |
    |   challenge_id . user_ref . selfie . id_photo
    |   liveness_frames[] . frame_binding . mrz_text
    |
    v
  +=== FAN OUT: all four layers run concurrently (asyncio.gather) ========+
  |                                                                       |
  |   face_match                                    in: selfie + id_photo |
  |      MTCNN --crop/align--> InceptionResnetV1 (vggface2)               |
  |            --> two 512-d embeddings --> cosine similarity             |
  |      out:  risk anchored so similarity == threshold  ->  0.50         |
  |            confidence = MTCNN face-detection probability              |
  |                                                                       |
  |   deepfake                                              in: selfie    |
  |      SigLIP2  OR  ViT   (load-either, identical interface)            |
  |            --> fake_probability                                       |
  |      out:  risk = fake_probability, confidence = max softmax          |
  |                                                                       |
  |   liveness                          in: frames + challenge  [DEMO]    |
  |      challenge fresh?  single-use?  HMAC frame-binding valid?         |
  |      inter-frame pixel delta above the static-image floor?            |
  |      out:  risk = worst finding, floored at 0.20; confidence 0.35     |
  |                                                                       |
  |   injection                         in: selfie + frames     [DEMO]    |
  |      EXIF provenance . frame uniformity . sensor-noise floor          |
  |      out:  risk = worst finding, floored at 0.30; confidence 0.20     |
  |                                                                       |
  +=== FAN IN: total latency = slowest layer, NOT the sum ================+
    |
    |   four LayerResults: { risk, confidence, ok, reason, demonstrator }
    v
  AGGREGATOR
    |
    |   weighted mean of the four risks; each layer's weight is
    |
    |         layer_weight  x  confidence  x  (0.5 if demonstrator)
    |
    |   so an unsure or deliberately weak layer moves the score less
    |   than a confident production-grade one
    v
  risk_score

    0.0 ------------- 0.30 ------------- 0.70 ------------- 1.0
     |    ALLOW        |    STEP_UP       |     DENY         |
     |                 |                  |                  |
     |  continue flow  |  OTP / passkey   |  fail path       |
     |                 |                  |                  |
     |   PROVISIONAL   |   PROVISIONAL    |   REJECTED       |
```

### Layers and models

| Layer | Model / method | Real? | Default |
|---|---|---|---|
| `face_match` | MTCNN → InceptionResnetV1 (`vggface2`) → cosine similarity | Real | **Off** (mock stub) |
| `deepfake` | SigLIP2 or ViT image classifier (load-either) | Real | **Off** (mock stub) |
| `liveness` | Challenge binding + inter-frame delta heuristics | Demonstrator | **On** |
| `injection` | EXIF provenance + sensor-noise heuristics | Demonstrator | **On** |

Model-backed layers are off by default so tests and startup stay fast and
network-free. Every result carries a `demonstrator` flag, and the aggregator
down-weights those layers.

**Checkpoints** (both Apache-2.0, ungated, cached to `.cache/huggingface/`):

| `model_choice` | Checkpoint | Base |
|---|---|---|
| `vit` (default) | `prithivMLmods/Deep-Fake-Detector-v2-Model` | `google/vit-base-patch16-224-in21k` |
| `siglip2` | `prithivMLmods/Deepfake-Detect-Siglip2` | `google/siglip2-base-patch16-224` |

Each checkpoint's native label order is normalised inside the layer into a
canonical `fake_probability`, so the aggregator never needs to know which is
active.

---

## Async tier pipeline

```text
   POST /verify (id_photo)        POST /verify/document/async
              |                              |
              '--------------.---------------'
                             |
                  job queued, response returns at once
                             |
                    in-process worker (asyncio queue)
                             |
                      mrz_text supplied?
                             |
            .----------------'----------------.
           yes                                no
            |                                 |
        MRZ parse                     tesseract installed?
     ICAO Doc 9303                            |
    TD1 / TD2 / TD3                  .--------'--------.
            |                       yes                no
    check digits valid?              |                 |
            |                    OCR image      nothing to check
      .-----'-----.                  |                 |
     no          yes                 |                 |
      |           |                  |                 |
      |           '------------------'-----------------'
      |                              |
  AUTO-REJECT                 AWAITING_REVIEW
  deterministic          human settles via POST /review
  no reviewer time                    |
      |                     .---------'---------.
      |                   ALLOW               DENY
      |                     |                   |
      v                     v                   v
  REJECTED              VERIFIED            REJECTED
      |                     |                   |
      '---------------------'-------------------'
                            |
              PUT /users/{id}  verification_status
                            |
                        ThunderID
```

Poll `GET /document/{job_id}` for status, findings and extracted MRZ fields.

---

## Deployment (PoC)

`./deployment/start-all.sh` runs both services on one machine, so TrustGate is
developed against the real identity product rather than a mock.

```text
  ONE MACHINE (macOS / Linux)

  +---------------------------+          +----------------------------+
  |  ThunderID  v1.0.1        |          |  TrustGate                 |
  |  Go binary + SQLite       |          |  uvicorn / FastAPI         |
  |  HTTPS :8090 self-signed  |          |  HTTP  :8000               |
  |                           |          |                            |
  |  /console       admin UI  |          |  /challenge   /verify      |
  |  /oauth2/token  OAuth2    |          |  /document    /review      |
  |  /users         mgmt API  |          |  /status      /health      |
  |  /health/liveness         |          |                            |
  +---------------------------+          +----------------------------+

  How TrustGate talks to ThunderID when a verification settles:

     TrustGate                                          ThunderID
         |                                                   |
    1    |  POST /oauth2/token                               |
         |  Basic(client_id, client_secret)                  |
         |  grant_type=client_credentials                    |
         |  scope=system   resource=<base>/mcp               |
         |-------------------------------------------------->|
         |<--------------------------------------------------|
         |  access_token   scope: system   (cached)          |
         |                                                   |
    2    |  GET /users?filter=username eq "<user_ref>"       |
         |-------------------------------------------------->|
         |<--------------------------------------------------|
         |  { id, ouId, type, attributes }   <- need all     |
         |                                                   |
    3    |  PUT /users/{id}       full replace, not a patch   |
         |  { ouId, type, attributes: { ..., status } }      |
         |-------------------------------------------------->|
         |<--------------------------------------------------|
         |  200 + stored attributes                          |
         |                                                   |
    4    |  read back and compare                            |
         |  a 200 does NOT mean it was stored -- an          |
         |  unregistered attribute is dropped silently       |
         |                                                   |
```

```text
  deployment/
    dist/                          ThunderID distribution  (gitignored ~115MB)
    run/                           logs + pid files        (gitignored)
    resources/trustgate-app.yaml   applied at ThunderID startup
    provision_thunderid.py         run once ThunderID is up
```

### Startup sequence

```text
  fetch-thunderid.sh          detect OS/arch -> download release -> unpack
          |
  start-all.sh
          |
          +-- (first run only) ThunderID setup.sh
          |       generates config/certs/, seeds resources, creates admin
          |       WITHOUT THIS: start.sh fails on missing crypto.key
          |
          +-- ThunderID start.sh <resources/trustgate-app.yaml>
          |       registers TrustGate m2m app + role granting `system`
          |       poll https://localhost:8090/health/liveness
          |
          +-- provision_thunderid.py
          |       adds verification_status to the Person user-type schema
          |       WITHOUT THIS: attribute writes return 200 and store nothing
          |
          +-- TrustGate uvicorn
                  poll http://127.0.0.1:8000/health
```

Both services are launched detached (`nohup`, own process group), so they
outlive the launching shell and can be stopped independently.

### Provisioning: two mechanisms, and why

| What | How | Why not the other way |
|---|---|---|
| TrustGate app + role | Declarative YAML passed to `start.sh` | Creating it via API needs a token, which needs the app — circular |
| `verification_status` on the schema | `provision_thunderid.py` (authenticated) | Cannot be expressed in the startup resources file |

See [deployment/README.md](deployment/README.md) for the full API contract and
four non-obvious behaviours that will otherwise bite.

---

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# TrustGate alone
uvicorn app.main:app --reload
curl http://127.0.0.1:8000/health          # {"status":"ok"}

# TrustGate + ThunderID together
./deployment/fetch-thunderid.sh            # one-time, ~34MB
./deployment/start-all.sh
./deployment/stop-all.sh
```

Optional — enables MRZ OCR (otherwise pass `mrz_text` yourself):

```bash
brew install tesseract && pip install pytesseract
```

Exercise the whole flow against a running server:

```bash
./scripts/demo.sh          # BASE_URL and USER_REF are overridable
```

---

## API reference

| Endpoint | Purpose |
|---|---|
| `POST /challenge` | Issue `{challenge_id, prompt_sequence, nonce, expires_at}` for the liveness handshake |
| `POST /verify` | Run the sync tier; return flat `VerifyResponse`; queue a doc job if `id_photo` present |
| `POST /verify/document/async` | Queue a doc job directly, outside the sync flow |
| `GET /document/{job_id}` | Doc job status, findings, extracted MRZ fields |
| `GET /status/{user_ref}` | Current verification state |
| `POST /review/{job_id}` | Human settle: `{decision, reviewer_note}` |

`/verify` form fields:

- **Required** — `challenge_id`, `user_ref`, `selfie`
- **Optional** — `id_photo`, `liveness_frames[]`, `frame_binding`, `mrz_text`

### Verification state machine

```text
                    sync tier passes            async review passes
   UNVERIFIED --------------------> PROVISIONAL -------------------> VERIFIED
       |                                 |
       | sync tier fails (DENY)          | async review fails
       |                                 |
       +------------> REJECTED <---------+
```

- `VERIFIED` and `REJECTED` are **terminal**
- `PROVISIONAL -> PROVISIONAL` is allowed, so a retried `/verify` does not fail

### Document job status

```text
   PENDING --> AWAITING_REVIEW --> VERIFIED | REJECTED
       |
       +-------> REJECTED          (automated checks failed)
```

A settled job cannot be reviewed again (`409`).

---

## Layer detail and honest limitations

Every layer self-reports. Read `demonstrator: true` as "this is deliberately
weak", never as a production control.

### Face match — real

- **MTCNN** detects, crops and aligns each face; **InceptionResnetV1**
  (`vggface2` by default, or `casia-webface`) embeds it
- Cosine similarity → risk, anchored so `similarity == threshold` maps to
  exactly `0.5` — a pair that fails the threshold can never score as ALLOW
- Confidence is MTCNN's **detection probability**, not distance from the
  threshold, so borderline comparisons keep full weight in the aggregator
- No face detected in either image → maximum risk, not an error
- Weights cache to `.cache/torch/` via `TORCH_HOME`
- MTCNN/InceptionResnetV1 are **vendored** into
  `app/layers/_vendor/facenet_pytorch/` (MIT) — the PyPI release pins
  `torch<2.3.0` / `numpy<2.0.0` / `Pillow<10.3.0`, which will not build on
  recent Python. See that directory's README for exactly what changed
  (nothing behavioural)

### Deepfake — real

- Load-either SigLIP2 / ViT classifier; emits `fake_probability` → risk
- **Treat as a risk signal, not a verdict** — it does not generalise to unseen
  generators
- Corrupt/undecodable input returns a graded result rather than a 500

### Liveness — demonstrator

**What it genuinely enforces, server-side:**

| Check | Catches |
|---|---|
| Challenge exists, unexpired | Attempts not tied to a fresh server-issued challenge |
| Challenge is single-use | Re-presenting an earlier attempt's `challenge_id` |
| Frame binding (HMAC, optional) | Wholesale replay of a previously captured payload |
| Frame count | Payloads too thin to assess |
| Inter-frame pixel delta | One still image submitted as a capture |

**Frame binding** = `HMAC(nonce, frames)`, sent as the optional `frame_binding`
field. It proves the payload was assembled by something holding *this*
challenge's nonce. It does **not** prove live capture — the nonce reaches the
client in the clear, so anyone who can request a challenge can bind
pre-recorded footage. Omitting it is scored as a real gap, not ignored.

**What it does NOT do:**

- **Does not verify the prompted actions happened.** The challenge asks for
  "blink, nod, open_mouth" and nothing checks that any of it occurred — that
  needs action recognition, out of scope here
- **Not a certified PAD control.** No iBeta or equivalent testing; none claimed
- **Motion check is crude.** Distinguishes a repeated still from *something*
  changing; cannot distinguish a live face from a video, mask or screen replay
- **A clean pass never scores zero** — floors at `baseline_risk` 0.2, with
  `confidence` capped at 0.35

### Injection — demonstrator, the weakest layer here

**Injection** = bypassing the camera entirely (virtual camera, emulator, direct
API feed), as distinct from a *presentation* attack held to a real lens.

Be clear-eyed: **detecting this from uploaded stills is close to a lost cause.**
Production systems solve it with **client attestation** (Play Integrity, App
Attest), proving frames came from a real camera on an untampered device. This
service receives an HTTP upload and can attest nothing about its origin.

| Signal | Rationale | Why it's weak |
|---|---|---|
| EXIF `Software` vs. known markers | Tooling often leaves a trace | Trivially stripped or spoofed |
| Missing camera make/model | Synthetic frames often carry none | Many clients strip EXIF for privacy |
| Missing capture timestamp | Incomplete provenance | Same |
| Identical frame byte lengths | Real frames rarely match exactly | Easy to vary |
| Sensor-noise floor | Real sensors are never perfectly smooth | A photo of a blank wall scores low |
| Frame dimension disagreement | One session should be consistent | Easy to normalise |

- Scored to be leaned on least: **lowest confidence (0.2)**, **highest baseline
  risk (0.3)**
- Expect **false positives** on privacy-conscious clients and **false
  negatives** on any attacker who forges metadata and adds noise
- Do not tune a hard gate on this layer's output

### MRZ check digits — the one deterministic signal

Everything else here is a score with a threshold. MRZ check digits are not:
ICAO Doc 9303 defines them, and they either verify or they do not.

- Parses **TD1** (3×30), **TD2** (2×36), **TD3** (2×44)
- Validated in the test suite against the **official specimen in Doc 9303
  Part 4** — checked against the standard, not merely against itself

**What a valid MRZ proves:** the fields agree with their check digits, i.e. the
transcription is internally consistent.

**What it does not prove:**

- **Not authenticity.** Check digits are a transcription-integrity mechanism.
  Anyone fabricating a document computes valid ones as a matter of course —
  which is exactly why a passing MRZ *escalates to human review* rather than
  approving anything
- **Not that tampering is caught.** Modulo-10 check digits are blind to any
  alteration whose weighted contribution shifts by a multiple of 10 — roughly
  **one random alteration in ten passes silently**. Worked example on the ICAO
  specimen in `tests/test_mrz.py`, kept as a test so it is not later mistaken
  for a bug

A **failing** check digit is unambiguous, so the worker rejects outright and
settles state without spending reviewer time.

**OCR** is optional and absent by default — tesseract is a system package, not a
pip dependency. With no `mrz_text` and no OCR, the job reports that nothing
could be checked and escalates; absence of a check is never reported as a pass.

---

## Integration contract

### 1. Inline (synchronous) — HTTP-request executor

`decision` and `risk_score` are deliberately **top-level, flat JSON**, so they
map onto a simple field path with no nesting to traverse:

```json
{
  "user_ref": "demo-user",
  "state": "PROVISIONAL",
  "decision": "ALLOW",
  "risk_score": 0.28,
  "reasons": ["injection: risk=0.56"],
  "layers": ["...per-layer detail..."],
  "document_job_id": "88632a8e-4dbe-49c4-9895-5efbd5b4fa19"
}
```

```text
responseMapping:
  decision   -> $.decision
  risk_score -> $.risk_score

branch on decision:
  ALLOW   -> continue flow
  STEP_UP -> step-up executor (OTP / passkey)
  DENY    -> fail path
```

- Respect the flow engine's `failOnError` semantics — decide fail-open vs
  fail-closed explicitly
- A config-level fail posture is **not yet implemented**; use the layers'
  `demonstrator` flags and confidences as the interim signal

### 2. Out-of-band (asynchronous) — user-attribute write

Implemented and **verified against a running ThunderID**, behind a narrow
adapter (`app/integrations/thunderid_client.py`).

- **Off by default** (`ThunderIdSettings.enabled = False`) — a null
  implementation logs intent and returns `False`, so "not configured" can never
  be mistaken for "written"
- Written on async settle: reviewer decision, or automated MRZ rejection

| | |
|---|---|
| Attribute | `verification_status` (configurable) |
| Values | `UNVERIFIED` / `PROVISIONAL` / `VERIFIED` / `REJECTED` |
| Source of truth | TrustGate's local state store |
| User matched by | `username` (configurable; `id` treats `user_ref` as the ThunderID user id) |

The value written is always `VerificationState.value` — the same enum the local
store uses, so the two cannot drift in representation.

**Three behaviours the adapter exists to handle** (all confirmed against a real
server, not read off the spec):

1. **Token requests need an RFC 8707 `resource` parameter**, else
   `invalid_target`
2. **`PUT /users/{id}` is a full replace** — requires `ouId` and `type`, so
   every update is a read-modify-write
3. **Unregistered attributes are dropped silently, with a `200`.** The adapter
   therefore **reads the attribute back** and treats a mismatch as failure,
   rather than reporting a success that did not happen

**Failure handling:**

- Transient failures retried with backoff
- Permanent failures fail fast (unknown user, ambiguous match, missing scope,
  rejected attribute)
- An ambiguous username match **refuses to write** — putting verification state
  on the wrong account is worse than not writing it
- Returns `False`, never raises: the local store is the system of record and the
  verification has already settled, so an unreachable ThunderID must not undo it
  or fail a reviewer's action

**Session/token caveat:** flipping `verification_status` out of band does not
change an already-issued token. Either the user picks it up on next
login/refresh, or the downstream app reads `verification_status` live when
gating sensitive actions.

---

## Tests

```bash
pytest             # fast suite, no network or model downloads
pytest -m slow     # real model inference; needs images you supply
```

`slow` tests skip cleanly when their images are absent — see
[samples/README.md](samples/README.md).
