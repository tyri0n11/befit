"""MCP OAuth authorization server (RFC 8414/7591/6749), backing MCPServer's
`auth_server_provider`.

The issued access/refresh tokens are plain befit JWTs — the exact ones
`POST /api/v1/auth/login` returns — so `app/mcp/context.py`'s existing
per-tool auth check needs no changes; it is now a second, redundant
validation of the same token already checked by the transport layer.

Registered clients live in Postgres (`oauth_clients`), not Redis: production
Redis is cache-only with no volume, and a client meant to "connect once" must
survive a restart. Authorization codes and the pending-login-request are
short-lived and single-use, so they use the exact Redis get-then-delete
pattern `app/services/google_oauth.py` already established for `state`.
"""

import json
import secrets
import time
from typing import NoReturn
from urllib.parse import urlencode

from pydantic import AnyUrl

from app.core.database import db
from app.core.redis import cache
from app.core.settings import settings
from app.models.user import UserStatus
from app.repositories.mcp_oauth import McpOAuthRepository
from app.repositories.user import UserRepository
from app.services.auth import AuthError, AuthService
from app.services.email import get_email_sender
from app.utils.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.server.auth.provider import TokenError as OAuthTokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

_PENDING_PREFIX = "mcp:oauth:pending:"
_CODE_PREFIX = "mcp:oauth:code:"
# RFC 6749 §4.1.2 recommends a short authorization-code lifetime; unlike
# OAUTH_STATE_TTL_SECONDS (how long a human has to type credentials), this is
# an internal implementation detail, not something an environment should tune.
_CODE_TTL_SECONDS = 120


def _raise_token_error(exc: AuthError) -> NoReturn:
    raise OAuthTokenError(error="invalid_grant", error_description=exc.message) from exc


class BefitOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Implements the SDK's provider protocol on top of befit's existing
    local-login auth. `authorize()` never itself checks credentials — it
    redirects to app/mcp/login.py's hand-rolled login page, which does."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with db.session() as session:
            client = await McpOAuthRepository(session).get_client(client_id)
        if client is None:
            return None
        return OAuthClientInformationFull(
            client_id=client.client_id,
            client_secret=client.client_secret,
            redirect_uris=[AnyUrl(u) for u in client.redirect_uris],
            grant_types=client.grant_types,
            token_endpoint_auth_method=client.token_endpoint_auth_method,
            scope=client.scope,
            client_name=client.client_name,
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        async with db.session() as session:
            await McpOAuthRepository(session).create_client(
                client_id=client_info.client_id,
                client_secret=client_info.client_secret,
                redirect_uris=[str(u) for u in (client_info.redirect_uris or [])],
                grant_types=client_info.grant_types,
                token_endpoint_auth_method=client_info.token_endpoint_auth_method,
                scope=client_info.scope,
                client_name=client_info.client_name,
            )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        request_id = secrets.token_urlsafe(32)
        payload = {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "state": params.state,
            "scopes": params.scopes,
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": params.resource,
        }
        await cache.client.set(
            _PENDING_PREFIX + request_id,
            json.dumps(payload),
            ex=settings.OAUTH_STATE_TTL_SECONDS,
        )
        return f"{settings.MCP_ISSUER_URL}/oauth/login?request_id={request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        raw = await cache.client.get(_CODE_PREFIX + authorization_code)
        if raw is None:
            return None
        data = json.loads(raw)
        if data["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=data["scopes"] or [],
            expires_at=data["expires_at"],
            client_id=data["client_id"],
            code_challenge=data["code_challenge"],
            redirect_uri=AnyUrl(data["redirect_uri"]),
            redirect_uri_provided_explicitly=data["redirect_uri_provided_explicitly"],
            resource=data["resource"],
            subject=data["subject"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if authorization_code.subject is None:
            raise OAuthTokenError(
                error="invalid_grant",
                error_description="authorization code has no subject",
            )
        async with db.session() as session:
            user = await UserRepository(session).get_by_id(
                int(authorization_code.subject)
            )
            if user is None or user.status is not UserStatus.ACTIVE:
                raise OAuthTokenError(
                    error="invalid_grant",
                    error_description="account is no longer available",
                )
            access = create_access_token(user.id, user.token_version)
            refresh = create_refresh_token(user.id, user.token_version)
        # Single-use: only deleted on a fully successful exchange, so a failed
        # PKCE check upstream (checked by the SDK before this is called) still
        # leaves the code alive for a legitimate retry.
        await cache.client.delete(_CODE_PREFIX + authorization_code.code)
        return OAuthToken(
            access_token=access,
            refresh_token=refresh,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            scope=" ".join(authorization_code.scopes) or None,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        try:
            claims = decode_token(refresh_token, "refresh")
        except TokenError:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=["user"],
            subject=str(claims.user_id),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        async with db.session() as session:
            service = AuthService(session, get_email_sender())
            try:
                pair = await service.refresh(refresh_token.token)
            except AuthError as exc:
                _raise_token_error(exc)
        return OAuthToken(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            scope=" ".join(scopes) or None,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        async with db.session() as session:
            service = AuthService(session, get_email_sender())
            try:
                user = await service.user_from_access_token(token)
            except AuthError:
                return None
        return AccessToken(
            token=token,
            client_id="befit",
            scopes=["user"],
            subject=str(user.id),
            # PyJWT already enforces `exp` inside decode_token — reaching this
            # line means the token verified as unexpired, so there is nothing
            # further for BearerAuthBackend's own expiry check to add.
            expires_at=None,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Not implemented — RevocationOptions(enabled=False), so the SDK
        never calls this. A signed-out session just lets its tokens expire;
        app-wide revocation is still available via users.token_version."""

    # exchange_identity_assertion is intentionally not overridden — the
    # Protocol's own default rejects it with TokenError("unsupported_grant_type"),
    # which is correct: SEP-990 enterprise-IdP flows aren't in scope for v1.


async def store_authorization_code(
    request_id: str,
    user_id: int,
) -> str | None:
    """Called by app/mcp/login.py on a successful login: consumes the pending
    request and mints a fresh authorization code for it. Returns the redirect
    URL to send the browser to, or None if the pending request already
    expired (the user took too long, or resubmitted a stale form)."""
    raw = await cache.client.get(_PENDING_PREFIX + request_id)
    if raw is None:
        return None
    pending = json.loads(raw)
    await cache.client.delete(_PENDING_PREFIX + request_id)

    code = secrets.token_urlsafe(32)
    code_payload = {
        "client_id": pending["client_id"],
        "scopes": pending["scopes"],
        "expires_at": time.time() + _CODE_TTL_SECONDS,
        "code_challenge": pending["code_challenge"],
        "redirect_uri": pending["redirect_uri"],
        "redirect_uri_provided_explicitly": pending["redirect_uri_provided_explicitly"],
        "resource": pending["resource"],
        "subject": str(user_id),
    }
    await cache.client.set(
        _CODE_PREFIX + code, json.dumps(code_payload), ex=_CODE_TTL_SECONDS
    )

    params = {"code": code}
    if pending["state"] is not None:
        params["state"] = pending["state"]
    separator = "&" if "?" in pending["redirect_uri"] else "?"
    return f"{pending['redirect_uri']}{separator}{urlencode(params)}"


async def load_pending_request(request_id: str) -> dict | None:
    """Read-only peek used by the GET login page to know the request is still
    valid (and to show which client is asking) without consuming it."""
    raw = await cache.client.get(_PENDING_PREFIX + request_id)
    return json.loads(raw) if raw is not None else None
