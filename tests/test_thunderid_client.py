import json

import httpx
import pytest

from app.core.config import ThunderIdSettings
from app.core.contracts import VerificationState
from app.integrations.thunderid_client import (
    NullThunderIdClient,
    ThunderIdError,
    ThunderIdHttpClient,
    build_thunderid_client,
)

USER_ID = "01900000-0000-7000-8000-000000000030"
OU_ID = "01900000-0000-7000-8000-000000000001"
BASE_ATTRIBUTES = {"username": "alice", "email": "alice@example.com"}


def make_settings(**overrides) -> ThunderIdSettings:
    return ThunderIdSettings(enabled=True, retry_backoff_seconds=0.0, **overrides)


class FakeThunderId:
    """Minimal stand-in for the parts of ThunderID this adapter touches.

    Mirrors the behaviours confirmed against the real server: the token needs a
    `resource` parameter, PUT is a full replace requiring ouId/type, and
    attributes not registered on the user type schema are dropped silently with
    a 200 response.
    """

    def __init__(self, *, granted_scope="system", schema_attributes=("verification_status",), token_status=200):
        self.granted_scope = granted_scope
        self.schema_attributes = set(schema_attributes)
        self.token_status = token_status
        self.attributes = dict(BASE_ATTRIBUTES)
        self.users = [{"id": USER_ID, "ouId": OU_ID, "type": "Person", "attributes": self.attributes}]
        self.token_requests: list[dict] = []
        self.put_bodies: list[dict] = []
        self.fail_next_put = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if path == "/oauth2/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            self.token_requests.append(form)
            if self.token_status != 200:
                return httpx.Response(self.token_status, text="nope")
            if "resource" not in form:
                return httpx.Response(400, json={"error": "invalid_target"})
            return httpx.Response(
                200, json={"access_token": "tok", "scope": self.granted_scope, "expires_in": 3600}
            )

        if request.headers.get("Authorization") != "Bearer tok":
            return httpx.Response(401, text="unauthorized")

        if path == "/users":
            filter_expr = request.url.params.get("filter", "")
            matched = [u for u in self.users if f'"{u["attributes"].get("username")}"' in filter_expr]
            return httpx.Response(200, json={"users": matched, "totalResults": len(matched)})

        if path == f"/users/{USER_ID}" and request.method == "GET":
            return httpx.Response(200, json=self.users[0])

        if path == f"/users/{USER_ID}" and request.method == "PUT":
            if self.fail_next_put > 0:
                self.fail_next_put -= 1
                return httpx.Response(503, text="temporarily unavailable")
            body = json.loads(request.content)
            self.put_bodies.append(body)
            if "ouId" not in body or "type" not in body:
                return httpx.Response(400, json={"error": "ouId and type are required"})
            # Silent-drop: only schema-registered attributes are stored.
            stored = {k: v for k, v in body["attributes"].items() if k in self.schema_attributes or k in BASE_ATTRIBUTES}
            self.users[0]["attributes"] = stored
            return httpx.Response(200, json={**self.users[0], "attributes": stored})

        return httpx.Response(404, text=f"unhandled {request.method} {path}")


def build_client(fake: FakeThunderId, settings: ThunderIdSettings | None = None) -> ThunderIdHttpClient:
    settings = settings or make_settings()
    transport = httpx.MockTransport(fake.handler)
    return ThunderIdHttpClient(settings, client=httpx.AsyncClient(transport=transport))


@pytest.mark.asyncio
async def test_updates_status_and_reports_success():
    fake = FakeThunderId()
    client = build_client(fake)

    assert await client.update_verification_status("alice", VerificationState.VERIFIED) is True
    assert fake.users[0]["attributes"]["verification_status"] == "VERIFIED"


@pytest.mark.asyncio
async def test_token_request_includes_the_resource_indicator():
    """Without it the real ThunderID rejects the request with invalid_target."""
    fake = FakeThunderId()
    client = build_client(fake)

    await client.update_verification_status("alice", VerificationState.VERIFIED)

    assert fake.token_requests[0]["resource"] == "https://localhost:8090/mcp"
    assert fake.token_requests[0]["grant_type"] == "client_credentials"


@pytest.mark.asyncio
async def test_existing_attributes_are_preserved():
    """PUT is a full replace, so a naive write would drop the other attributes."""
    fake = FakeThunderId()
    client = build_client(fake)

    await client.update_verification_status("alice", VerificationState.VERIFIED)

    assert fake.users[0]["attributes"]["email"] == "alice@example.com"
    assert fake.users[0]["attributes"]["username"] == "alice"


@pytest.mark.asyncio
async def test_update_sends_ou_id_and_type():
    fake = FakeThunderId()
    client = build_client(fake)

    await client.update_verification_status("alice", VerificationState.VERIFIED)

    body = fake.put_bodies[0]
    assert body["ouId"] == OU_ID
    assert body["type"] == "Person"


@pytest.mark.asyncio
async def test_silently_dropped_attribute_is_reported_as_failure():
    """The trap this adapter exists to guard: ThunderID answers 200 and stores
    nothing when the attribute is not on the user type schema.
    """
    fake = FakeThunderId(schema_attributes=())  # verification_status not registered
    client = build_client(fake)

    assert await client.update_verification_status("alice", VerificationState.VERIFIED) is False


@pytest.mark.asyncio
async def test_token_without_the_system_scope_is_rejected():
    """Declaring scopes on the app is not the same as being granted them."""
    fake = FakeThunderId(granted_scope="")
    client = build_client(fake)

    assert await client.update_verification_status("alice", VerificationState.VERIFIED) is False


@pytest.mark.asyncio
async def test_unknown_user_does_not_write_anything():
    fake = FakeThunderId()
    client = build_client(fake)

    assert await client.update_verification_status("nobody", VerificationState.VERIFIED) is False
    assert fake.put_bodies == []


@pytest.mark.asyncio
async def test_ambiguous_match_refuses_rather_than_guessing():
    """Writing verification state onto the wrong account is worse than not writing it."""
    fake = FakeThunderId()
    fake.users.append({"id": "other", "ouId": OU_ID, "type": "Person", "attributes": {"username": "alice"}})
    client = build_client(fake)

    assert await client.update_verification_status("alice", VerificationState.VERIFIED) is False
    assert fake.put_bodies == []


@pytest.mark.asyncio
async def test_transient_failure_is_retried_then_succeeds():
    fake = FakeThunderId()
    fake.fail_next_put = 2
    client = build_client(fake, make_settings(max_attempts=3))

    assert await client.update_verification_status("alice", VerificationState.VERIFIED) is True
    assert fake.users[0]["attributes"]["verification_status"] == "VERIFIED"


@pytest.mark.asyncio
async def test_persistent_failure_returns_false_without_raising():
    """A settled verification must not be undone by ThunderID being unreachable,
    so the caller gets False rather than an exception.
    """
    fake = FakeThunderId()
    fake.fail_next_put = 99
    client = build_client(fake, make_settings(max_attempts=2))

    assert await client.update_verification_status("alice", VerificationState.VERIFIED) is False


@pytest.mark.asyncio
async def test_token_is_reused_across_calls():
    fake = FakeThunderId()
    client = build_client(fake)

    await client.update_verification_status("alice", VerificationState.VERIFIED)
    await client.update_verification_status("alice", VerificationState.REJECTED)

    assert len(fake.token_requests) == 1


@pytest.mark.asyncio
async def test_lookup_by_id_skips_the_filter_query():
    fake = FakeThunderId()
    client = build_client(fake, make_settings(user_lookup_attribute="id"))

    assert await client.update_verification_status(USER_ID, VerificationState.VERIFIED) is True


@pytest.mark.asyncio
async def test_rejected_state_is_written_too():
    fake = FakeThunderId()
    client = build_client(fake)

    await client.update_verification_status("alice", VerificationState.REJECTED)

    assert fake.users[0]["attributes"]["verification_status"] == "REJECTED"


@pytest.mark.asyncio
async def test_null_client_reports_false_not_success():
    """"Not configured" must never be mistaken for "written"."""
    assert await NullThunderIdClient().update_verification_status("alice", VerificationState.VERIFIED) is False


def test_factory_returns_null_client_when_disabled():
    assert isinstance(build_thunderid_client(ThunderIdSettings(enabled=False)), NullThunderIdClient)


def test_factory_returns_http_client_when_enabled():
    assert isinstance(build_thunderid_client(ThunderIdSettings(enabled=True)), ThunderIdHttpClient)


@pytest.mark.asyncio
async def test_error_message_names_the_schema_as_the_likely_cause():
    """The silent-drop failure is obscure, so the message must point at it."""
    fake = FakeThunderId(schema_attributes=())
    client = build_client(fake, make_settings(max_attempts=1))

    with pytest.raises(ThunderIdError, match="user type schema"):
        await client._write_status("alice", VerificationState.VERIFIED)


@pytest.mark.asyncio
async def test_permanent_failures_are_not_retried():
    """A missing user fails identically every time, so it must not burn the
    retry budget (and fill the log with pointless retry warnings).
    """
    fake = FakeThunderId()
    client = build_client(fake, make_settings(max_attempts=3))

    assert await client.update_verification_status("nobody", VerificationState.VERIFIED) is False
    # One lookup, not three.
    assert len(fake.token_requests) == 1


@pytest.mark.asyncio
async def test_transient_failures_still_use_the_full_retry_budget():
    fake = FakeThunderId()
    fake.fail_next_put = 2
    client = build_client(fake, make_settings(max_attempts=3))

    assert await client.update_verification_status("alice", VerificationState.VERIFIED) is True
    assert len(fake.put_bodies) == 1  # only the successful attempt records a body
