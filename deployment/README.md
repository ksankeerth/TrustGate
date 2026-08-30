# deployment/

Runs **ThunderID** and **TrustGate** together on one machine, so TrustGate can
be developed against the real identity product rather than a mock of it.

## Usage

```bash
./deployment/fetch-thunderid.sh   # one-time: download + unpack (~34MB)
./deployment/start-all.sh         # setup (first run), start both, provision, wait until serving
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
are gitignored; only the scripts, the resources file and this README are
committed.

## What the scripts do

**`fetch-thunderid.sh`** detects your OS/arch, downloads the matching release
from `github.com/thunder-id/thunderid`, and unpacks it. Idempotent — it skips
the download if the distribution is already there, and reuses a partial archive.

**`start-all.sh`**

1. On first run only, runs ThunderID's own `setup.sh`, which generates key
   material into `config/certs/`, seeds default resources and creates the admin
   user. **This step is not optional**: without it `start.sh` fails looking for
   `config/certs/crypto.key`. A marker file records that it has been done.
2. Starts ThunderID via its `start.sh`, passing `resources/trustgate-app.yaml`
   so the TrustGate application and its role are registered, and polls
   `/health/liveness`.
3. Runs `provision_thunderid.py` to register the `verification_status`
   attribute on the user-type schema — see "Four things that are easy to get
   wrong" below for why skipping this fails silently.
4. Starts TrustGate via uvicorn and polls `/health`.

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
but that is only half the story: ThunderID still validates it against the user
type's schema and drops anything undeclared — see point 2 below.

The whole path below has been verified against a running ThunderID v1.0.1, not
inferred from the spec alone.

### Getting a token

```bash
curl -sk -X POST https://localhost:8090/oauth2/token \
  -u "TRUSTGATE:trustgate-local-client-secret" \
  -d grant_type=client_credentials \
  -d scope=system \
  -d resource=https://localhost:8090/mcp
```

The **`resource` parameter is required** (RFC 8707 Resource Indicators). Without
it the token endpoint returns `invalid_target: No resource parameter supplied
and no default resource server is configured`. Its value is the System resource
server's identifier, `https://localhost:8090/mcp`.

### Four things that are easy to get wrong

**1. `PUT /users/{id}` is a full replace, not a patch.** Sending only
`attributes` is rejected with `400` — `ouId` and `type` are required — so the
adapter must read the user first and write back the merged result.

**2. Unregistered attributes are dropped silently, with a `200`.** ThunderID
validates user attributes against the user type's schema. A `PUT` carrying a
`verification_status` that is not declared on the `Person` schema **succeeds,
returns 200, and stores nothing**. There is no error to notice. This is what
`provision_thunderid.py` exists to prevent, and `start-all.sh` runs it on every
start; it is idempotent and fails loudly if the attribute cannot be registered.

**3. Declaring `scopes` on the application does not grant them.** The `scopes`
list only says what the client may *ask for*. Access is role-based: a role
carries permissions on a resource server, and entities are assigned to it with
`type: app`. Without the role the token is issued successfully but carries no
`scope` claim, and the API answers `403`. `resources/trustgate-app.yaml`
defines a dedicated `TrustGate Service` role rather than assigning TrustGate to
the built-in `Administrator` role, so its access can be inspected and revoked
on its own.

**4. `Direct-Auth-Secret` does not work here.** ThunderID has a Direct API
gated by that header, and it is tempting to reach for. It does not authenticate
the management APIs — `/users` answers `401`. Use OAuth2.

### Provisioning

`resources/trustgate-app.yaml` registers the application and its role
declaratively; `start-all.sh` passes it to ThunderID at startup, so it needs no
credentials and applies on every run.

The user-type schema change cannot be declarative — it needs an authenticated
call — so `provision_thunderid.py` does it after ThunderID is up:

```bash
python3 deployment/provision_thunderid.py    # idempotent; start-all.sh runs it for you
```

## Local HTTPS

ThunderID serves HTTPS with a self-signed certificate, so `curl` needs `-k`
(and an HTTP client needs verification disabled) when talking to it locally.
