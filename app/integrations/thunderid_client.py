"""Adapter for pushing settled verification state into ThunderID.

This is the out-of-band half of the integration: the sync tier answers inside
the login flow, and later -- once document review settles, with no flow
running -- the user's `verification_status` attribute is updated here.

Kept behind a narrow interface (`ThunderIdClient`) so the orchestration code
never depends on ThunderID's specifics, and so a deployment without ThunderID
can drop in the null implementation and change nothing else.

Three behaviours here exist because of how ThunderID actually behaves, verified
against a running server rather than read off the spec:

1. Token requests must carry an RFC 8707 `resource` parameter, or the token
   endpoint answers `invalid_target`.
2. `PUT /users/{id}` is a full replace: it requires `ouId` and `type`, and
   rejects a body carrying only `attributes`. So every update is a
   read-modify-write.
3. ThunderID validates attributes against the user type's schema and **silently
   discards** undeclared ones -- returning 200 while storing nothing. An
   unverified write here would look successful and do nothing, so the update is
   read back and confirmed before it is reported as having succeeded.
"""

import asyncio
import logging
import time
from typing import Protocol

import httpx

from app.core.config import ThunderIdSettings, default_thunderid_settings
from app.core.contracts import VerificationState

logger = logging.getLogger(__name__)


class ThunderIdError(RuntimeError):
    """The verification status could not be written to ThunderID.

    Treated as transient: worth retrying (a timeout, a 5xx, a blip).
    """


class ThunderIdPermanentError(ThunderIdError):
    """A failure that retrying cannot fix.

    A missing user, an ambiguous match, a misconfigured scope or an attribute
    the schema does not accept will fail identically every time, so these are
    reported immediately rather than burning the retry budget.
    """


class ThunderIdClient(Protocol):
    async def update_verification_status(self, user_ref: str, state: VerificationState) -> bool:
        """Set the user's verification status. Returns True if it was written."""
        ...


class NullThunderIdClient:
    """Used when the integration is disabled: records intent, touches nothing.

    Returns False rather than True so callers cannot mistake "not configured"
    for "written".
    """

    async def update_verification_status(self, user_ref: str, state: VerificationState) -> bool:
        logger.info(
            "ThunderID integration disabled; not propagating %s for user_ref=%s", state.value, user_ref
        )
        return False


class ThunderIdHttpClient:
    def __init__(
        self,
        settings: ThunderIdSettings = default_thunderid_settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            verify=settings.verify_tls,
            timeout=settings.timeout_seconds,
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- auth ---------------------------------------------------------------

    async def _get_token(self) -> str:
        # Locked so a burst of settles does not trigger concurrent token
        # requests that each overwrite the cached value.
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token

            response = await self._client.post(
                f"{self._settings.base_url}/oauth2/token",
                auth=(self._settings.client_id, self._settings.client_secret),
                data={
                    "grant_type": "client_credentials",
                    "scope": self._settings.scope,
                    "resource": self._settings.resource,
                },
            )
            if response.status_code != 200:
                raise ThunderIdError(f"token request failed ({response.status_code}): {response.text}")

            payload = response.json()
            token = payload.get("access_token")
            if not token:
                raise ThunderIdError(f"token response contained no access_token: {payload}")

            granted = payload.get("scope") or ""
            if self._settings.scope not in granted.split():
                # Declaring scopes on the application is not the same as being
                # granted them -- that needs a role assignment. Without it the
                # token is issued happily and every management call 403s.
                raise ThunderIdPermanentError(
                    f"token was issued without the {self._settings.scope!r} scope (got {granted!r}); "
                    "the TrustGate application needs a role granting that permission"
                )

            expires_in = float(payload.get("expires_in", 3600))
            self._token = token
            self._token_expires_at = time.monotonic() + max(
                0.0, expires_in - self._settings.token_expiry_margin_seconds
            )
            return token

    async def _authorized(self, method: str, path: str, **kwargs) -> httpx.Response:
        token = await self._get_token()
        return await self._client.request(
            method,
            f"{self._settings.base_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            **kwargs,
        )

    # --- user lookup and update ---------------------------------------------

    async def _find_user(self, user_ref: str) -> dict:
        if self._settings.user_lookup_attribute == "id":
            response = await self._authorized("GET", f"/users/{user_ref}")
            if response.status_code == 404:
                raise ThunderIdPermanentError(f"no ThunderID user with id {user_ref!r}")
            if response.status_code != 200:
                raise ThunderIdError(f"user lookup failed ({response.status_code}): {response.text}")
            return response.json()

        attribute = self._settings.user_lookup_attribute
        response = await self._authorized(
            "GET", "/users", params={"filter": f'{attribute} eq "{user_ref}"', "limit": 2}
        )
        if response.status_code != 200:
            raise ThunderIdError(f"user lookup failed ({response.status_code}): {response.text}")

        users = response.json().get("users") or []
        if not users:
            raise ThunderIdPermanentError(f"no ThunderID user with {attribute}={user_ref!r}")
        if len(users) > 1:
            # Refusing beats guessing: writing verification state onto the
            # wrong account is worse than not writing it.
            raise ThunderIdPermanentError(
                f"{attribute}={user_ref!r} matched {len(users)} ThunderID users; refusing to guess"
            )
        return users[0]

    async def _write_status(self, user_ref: str, state: VerificationState) -> bool:
        user = await self._find_user(user_ref)
        user_id = user["id"]

        # Full replace, not a patch: ouId and type must be sent back, and the
        # existing attributes merged, or they would be lost.
        attributes = dict(user.get("attributes") or {})
        attributes[self._settings.attribute_name] = state.value
        body = {"ouId": user["ouId"], "type": user["type"], "attributes": attributes}

        response = await self._authorized("PUT", f"/users/{user_id}", json=body)
        if response.status_code != 200:
            raise ThunderIdError(f"user update failed ({response.status_code}): {response.text}")

        # Read back: a 200 here does not mean the attribute was stored. If it is
        # not registered on the user type's schema, ThunderID drops it without
        # complaint, and reporting success would be a lie.
        written = (response.json().get("attributes") or {}).get(self._settings.attribute_name)
        if written != state.value:
            raise ThunderIdPermanentError(
                f"ThunderID accepted the update but did not store "
                f"{self._settings.attribute_name!r} (got {written!r}, expected {state.value!r}); "
                f"the attribute is most likely not registered on the {user['type']!r} user type schema"
            )
        return True

    async def update_verification_status(self, user_ref: str, state: VerificationState) -> bool:
        last_error: Exception | None = None

        for attempt in range(1, self._settings.max_attempts + 1):
            try:
                await self._write_status(user_ref, state)
                logger.info("set %s=%s for user_ref=%s in ThunderID", self._settings.attribute_name, state.value, user_ref)
                return True
            except ThunderIdPermanentError as exc:
                # Retrying cannot help; fail fast rather than spending the budget.
                logger.error("cannot propagate %s for user_ref=%s to ThunderID: %s", state.value, user_ref, exc)
                return False
            except (httpx.HTTPError, ThunderIdError) as exc:
                last_error = exc
                if attempt < self._settings.max_attempts:
                    delay = self._settings.retry_backoff_seconds * attempt
                    logger.warning(
                        "ThunderID update for user_ref=%s failed (attempt %d/%d), retrying in %.1fs: %s",
                        user_ref, attempt, self._settings.max_attempts, delay, exc,
                    )
                    await asyncio.sleep(delay)

        # Never raised onward: the local state store is the system of record, so
        # a settled verification must not be undone by ThunderID being
        # unreachable. Surfaced loudly instead, for retry or reconciliation.
        logger.error(
            "gave up propagating %s for user_ref=%s to ThunderID after %d attempts: %s",
            state.value, user_ref, self._settings.max_attempts, last_error,
        )
        return False


def build_thunderid_client(settings: ThunderIdSettings = default_thunderid_settings) -> ThunderIdClient:
    return ThunderIdHttpClient(settings) if settings.enabled else NullThunderIdClient()
