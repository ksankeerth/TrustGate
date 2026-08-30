#!/usr/bin/env python3
"""Provision ThunderID so TrustGate can write verification state to it.

Registering the TrustGate application and its role is declarative (see
resources/trustgate-app.yaml, passed to ThunderID at startup). This script
handles the one part that cannot be: extending the user-type schema, which
requires an authenticated call.

Why the schema step is not optional: ThunderID validates user attributes
against the user type's schema and **silently discards** anything not declared
there. A PUT carrying an unregistered `verification_status` returns 200 OK and
stores nothing, so without this step TrustGate's writes would appear to succeed
while doing nothing at all.

Idempotent: safe to re-run.
"""

import argparse
import json
import ssl
import sys
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://localhost:8090"
DEFAULT_CLIENT_ID = "TRUSTGATE"
DEFAULT_CLIENT_SECRET = "trustgate-local-client-secret"
# The System resource server's identifier. ThunderID requires it as the RFC 8707
# `resource` parameter; without it the token endpoint returns invalid_target.
DEFAULT_RESOURCE = "https://localhost:8090/mcp"
DEFAULT_USER_TYPE = "Person"

VERIFICATION_STATUS_ATTRIBUTE = "verification_status"
VERIFICATION_STATUS_SCHEMA = {
    "type": "string",
    "displayName": "Verification Status",
    "required": False,
}

# ThunderID serves HTTPS with a self-signed certificate locally.
_INSECURE_CONTEXT = ssl.create_default_context()
_INSECURE_CONTEXT.check_hostname = False
_INSECURE_CONTEXT.verify_mode = ssl.CERT_NONE


def _request(method: str, url: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict | None]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if data:
        request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, context=_INSECURE_CONTEXT) as response:
            payload = response.read()
            return response.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            return exc.code, json.loads(payload)
        except Exception:
            return exc.code, {"raw": payload.decode(errors="replace")}


def get_token(base_url: str, client_id: str, client_secret: str, resource: str) -> str:
    form = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "scope": "system", "resource": resource}
    ).encode()
    request = urllib.request.Request(f"{base_url}/oauth2/token", data=form, method="POST")
    import base64

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    request.add_header("Authorization", f"Basic {basic}")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(request, context=_INSECURE_CONTEXT) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"could not obtain a token ({exc.code}): {exc.read().decode(errors='replace')}\n"
            "Is ThunderID running, and was it started with deployment/resources/trustgate-app.yaml?"
        ) from exc

    if "system" not in (payload.get("scope") or ""):
        raise SystemExit(
            f"token was issued without the 'system' scope (got {payload.get('scope')!r}).\n"
            "The TrustGate application needs a role granting the system permission."
        )
    return payload["access_token"]


def find_user_type(base_url: str, token: str, name: str) -> dict:
    status, payload = _request("GET", f"{base_url}/user-types", token=token)
    if status != 200:
        raise SystemExit(f"could not list user types ({status}): {payload}")

    types = payload.get("types") or []
    for user_type in types:
        if user_type.get("name") == name:
            return user_type
    raise SystemExit(f"user type {name!r} not found; available: {[t.get('name') for t in types]}")


def ensure_verification_status_attribute(base_url: str, token: str, user_type_name: str) -> bool:
    """Add verification_status to the user type schema. Returns True if changed."""
    summary = find_user_type(base_url, token, user_type_name)
    type_id = summary["id"]

    status, user_type = _request("GET", f"{base_url}/user-types/{type_id}", token=token)
    if status != 200:
        raise SystemExit(f"could not read user type {type_id} ({status}): {user_type}")

    schema = dict(user_type.get("schema") or {})
    if VERIFICATION_STATUS_ATTRIBUTE in schema:
        print(f"  '{VERIFICATION_STATUS_ATTRIBUTE}' already present on user type {user_type_name!r}")
        return False

    schema[VERIFICATION_STATUS_ATTRIBUTE] = VERIFICATION_STATUS_SCHEMA
    # id and category are immutable on update; send everything else back so the
    # rest of the type definition survives the write.
    body = {k: v for k, v in user_type.items() if k not in ("id", "category")}
    body["schema"] = schema

    status, response = _request("PUT", f"{base_url}/user-types/{type_id}", token=token, body=body)
    if status != 200:
        raise SystemExit(f"could not update user type ({status}): {response}")

    print(f"  added '{VERIFICATION_STATUS_ATTRIBUTE}' to user type {user_type_name!r}")
    return True


def verify(base_url: str, token: str, user_type_name: str) -> None:
    summary = find_user_type(base_url, token, user_type_name)
    _status, user_type = _request("GET", f"{base_url}/user-types/{summary['id']}", token=token)
    if VERIFICATION_STATUS_ATTRIBUTE not in (user_type.get("schema") or {}):
        raise SystemExit(
            f"verification failed: {VERIFICATION_STATUS_ATTRIBUTE!r} is still absent from the "
            f"{user_type_name!r} schema, so attribute writes would be silently discarded"
        )
    print(f"  verified: {VERIFICATION_STATUS_ATTRIBUTE!r} is registered on {user_type_name!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--client-secret", default=DEFAULT_CLIENT_SECRET)
    parser.add_argument("--resource", default=DEFAULT_RESOURCE)
    parser.add_argument("--user-type", default=DEFAULT_USER_TYPE)
    args = parser.parse_args()

    print(f"Provisioning ThunderID at {args.base_url}")
    token = get_token(args.base_url, args.client_id, args.client_secret, args.resource)
    print("  obtained a client-credentials token with the 'system' scope")

    ensure_verification_status_attribute(args.base_url, token, args.user_type)
    verify(args.base_url, token, args.user_type)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
