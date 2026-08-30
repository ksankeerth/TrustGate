# deployment/

Runs **ThunderID** and **TrustGate** together on one machine, so TrustGate can
be developed against the real identity product rather than a mock of it.

## Usage

```bash
./deployment/fetch-thunderid.sh   # one-time: download + unpack (~34MB)
./deployment/start-all.sh         # start both, wait until serving
./deployment/stop-all.sh          # stop both
```

| | URL |
|---|---|
| ThunderID console | https://localhost:8090/console |
| ThunderID API | https://localhost:8090 |
| TrustGate API | http://127.0.0.1:8000 |
| TrustGate OpenAPI | http://127.0.0.1:8000/docs |

Default admin is `admin` / `Admin@123` — a local demo credential, set in
`config.sh`. Logs and pid files go to `deployment/run/`.

Everything is overridable from the environment:

```bash
THUNDERID_VERSION=1.0.2 ./deployment/fetch-thunderid.sh
TRUSTGATE_PORT=9000 THUNDERID_ADMIN_PASSWORD='...' ./deployment/start-all.sh
```

`deployment/dist/` (the ~115MB unpacked distribution) and `deployment/run/`
are gitignored; only the four scripts are committed.

## What the scripts do

**`fetch-thunderid.sh`** detects your OS/arch, downloads the matching release
from `github.com/thunder-id/thunderid`, and unpacks it. Idempotent — it skips
the download if the distribution is already there, and reuses a partial archive.

**`start-all.sh`**

1. On first run only, runs ThunderID's own `setup.sh`, which generates key
   material into `config/certs/`, seeds default resources and creates the admin
   user. **This step is not optional**: without it `start.sh` fails looking for
   `config/certs/crypto.key`. A marker file records that it has been done.
2. Starts ThunderID via its `start.sh` and polls `/health/liveness`.
3. Starts TrustGate via uvicorn and polls `/health`.

Both are launched with `nohup` and detached from stdin, so they outlive the
launching shell and terminal. If either fails to come up, the script prints the
tail of its log and exits non-zero.

> **Don't delete `config/certs/`.** ThunderID generates it during setup, and
> warns that if those keys are lost or changed, previously issued tokens and
> encrypted data can no longer be validated or decrypted.

**`stop-all.sh`** signals each process group, escalating to `SIGKILL` after 15s.
Because ThunderID's `start.sh` spawns the server as a child and waits on it, the
recorded pid is the wrapper — so the script also falls back to stopping whatever
holds the port. That covers a server orphaned by a killed launcher, which would
otherwise keep the port and block the next start.

## ThunderID User Management API

Taken from ThunderID's own OpenAPI spec (`api/user.yaml` in its repository),
not inferred from other vendors' documentation.

**It is not SCIM 2.0.** It is a native REST API under `/users`.

| | |
|---|---|
| Base URL | `https://localhost:8090` |
| Update a user | `PUT /users/{id}` |
| Find users | `GET /users?filter=username eq "..."` |
| Auth | OAuth2, scope `system` |
| Token endpoint | `POST https://localhost:8090/oauth2/token` |
| Flows | `client_credentials` and `authorization_code` |

`PUT /users/{id}` takes:

```json
{
  "ouId": "<organization unit uuid>",
  "type": "<user type>",
  "attributes": { "verification_status": "VERIFIED" }
}
```

`attributes` is a free-form object (`additionalProperties: true` in the spec),
so `verification_status` fits as a custom attribute without a schema change.

Two things to get right when the adapter is built:

- **`PUT` is a full replace, not a patch.** Sending only `attributes` risks
  dropping the user's other fields, so the adapter must read the user first and
  write back the merged result.
- **A client-credentials application must be registered.** Bootstrap creates
  only the `CONSOLE` application, which is an authorization-code client for the
  admin console. TrustGate needs its own application with the `system` scope;
  register it through the console at https://localhost:8090/console.

## Local HTTPS

ThunderID serves HTTPS with a self-signed certificate, so `curl` needs `-k`
(and an HTTP client needs verification disabled) when talking to it locally.
