"""Google OAuth authorization code flow.

Two things here are security-critical and easy to get wrong:

* **state** is single-use. It is stored in Redis and deleted when consumed, so a
  captured callback URL cannot be replayed. Verifying a signed value would not be
  enough — verification alone is repeatable.
* **id_token is verified, never trusted.** The signature is checked against
  Google's JWKS, and `aud` must equal our client id. Skipping the `aud` check
  would let an id_token minted for a *different* application log someone in here.
"""

import base64
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient
from redis.asyncio import Redis

from app.core.settings import settings

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = "openid email profile"
CALENDAR_SCOPES = f"{SCOPES} https://www.googleapis.com/auth/calendar.events"

_STATE_PREFIX = "oauth:google:state:"
_CALENDAR_STATE_PREFIX = "oauth:google:calendar:state:"
_REQUEST_TIMEOUT = httpx.Timeout(10.0)

# Reused across calls so the signing keys are cached rather than refetched on
# every callback.
_jwk_client = PyJWKClient(JWKS_URL, cache_keys=True)


class GoogleOAuthError(Exception):
    """The flow could not be completed. Message is safe to show a caller."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    email_verified: bool
    display_name: str


@dataclass(frozen=True)
class CalendarTokens:
    """Google's token response, kept in full — unlike GoogleIdentity, which
    only ever needed the id_token. `expires_in` is seconds from issuance."""

    user_id: int
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def start_login(redis: Redis) -> tuple[str, str]:
    """Store a fresh state + PKCE verifier and return (consent_url, state).

    The state is returned so a caller can correlate it with something of its
    own — e.g. app/mcp/login.py maps it to a pending MCP authorization
    request, since Google only allows one registered redirect URI and both
    flows must therefore share app/api/v1/endpoints/auth.py's callback.
    """
    if not settings.GOOGLE_OAUTH_CONFIGURED:
        raise GoogleOAuthError("Google sign-in is not configured")

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    await redis.set(
        _STATE_PREFIX + state, verifier, ex=settings.OAUTH_STATE_TTL_SECONDS
    )

    query = urlencode(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            # Without this Google omits a refresh token on repeat consent; we do
            # not use one, but being explicit documents the intent.
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return f"{AUTHORIZE_URL}?{query}", state


async def _consume_state(redis: Redis, state: str) -> str:
    """Return the PKCE verifier for `state` and delete it. Single-use."""
    key = _STATE_PREFIX + state
    verifier = await redis.get(key)
    if verifier is None:
        raise GoogleOAuthError("This sign-in link has expired or was already used")
    await redis.delete(key)
    return verifier


async def _exchange_code_full(code: str, verifier: str, redirect_uri: str) -> dict:
    """Swap the authorization code for the full token response."""
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )

    if response.is_error:
        # Google's body names the misconfiguration (redirect_uri_mismatch,
        # invalid_client, ...), which is worth having in the log.
        logger.error(
            "google token exchange failed: %s %s", response.status_code, response.text
        )
        raise GoogleOAuthError("Google rejected the sign-in attempt")

    return response.json()


async def _exchange_code(code: str, verifier: str) -> str:
    """Swap the authorization code for an id_token."""
    body = await _exchange_code_full(code, verifier, settings.GOOGLE_REDIRECT_URI)
    id_token = body.get("id_token")
    if not id_token:
        logger.error("google token response carried no id_token")
        raise GoogleOAuthError("Google rejected the sign-in attempt")
    return id_token


def verify_id_token(id_token: str) -> GoogleIdentity:
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.GOOGLE_CLIENT_ID,
            issuer=list(ISSUERS),
        )
    except jwt.PyJWTError as exc:
        logger.error("google id_token failed verification: %s", exc)
        raise GoogleOAuthError("Google sign-in could not be verified") from exc

    subject = claims.get("sub")
    email = claims.get("email")
    if not subject or not email:
        raise GoogleOAuthError("Google did not return an email for this account")

    return GoogleIdentity(
        subject=subject,
        email=email,
        # Google sends this as a real bool, but older clients sent "true".
        email_verified=claims.get("email_verified") in (True, "true"),
        display_name=claims.get("name") or email.split("@")[0],
    )


async def complete_login(redis: Redis, code: str, state: str) -> GoogleIdentity:
    verifier = await _consume_state(redis, state)
    id_token = await _exchange_code(code, verifier)
    return verify_id_token(id_token)


async def start_calendar_connect(redis: Redis, user_id: int) -> tuple[str, str]:
    """Same state/PKCE mechanics as start_login, but ties the state to the
    already-authenticated user who initiated it (stored as JSON, since this
    flow needs to recover `user_id` on the callback, not just the verifier)
    and requests calendar.events + offline access on top of identity scopes.
    `prompt=consent` forces Google to reissue a refresh_token even on a
    repeat connect, which access_type=offline alone does not guarantee."""
    if not settings.GOOGLE_CALENDAR_CONFIGURED:
        raise GoogleOAuthError("Google Calendar sync is not configured")

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    await redis.set(
        _CALENDAR_STATE_PREFIX + state,
        json.dumps({"verifier": verifier, "user_id": user_id}),
        ex=settings.OAUTH_STATE_TTL_SECONDS,
    )

    query = urlencode(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_CALENDAR_REDIRECT_URI,
            "response_type": "code",
            "scope": CALENDAR_SCOPES,
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return f"{AUTHORIZE_URL}?{query}", state


async def complete_calendar_connect(
    redis: Redis, code: str, state: str
) -> CalendarTokens:
    key = _CALENDAR_STATE_PREFIX + state
    raw = await redis.get(key)
    if raw is None:
        raise GoogleOAuthError("This connect link has expired or was already used")
    await redis.delete(key)
    stored = json.loads(raw)

    body = await _exchange_code_full(
        code, stored["verifier"], settings.GOOGLE_CALENDAR_REDIRECT_URI
    )
    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")
    if not access_token or not refresh_token:
        # Missing refresh_token usually means the user connected before and
        # prompt=consent somehow didn't re-issue one — nothing we can do but
        # surface it rather than store a connection with no way to refresh.
        logger.error("google calendar token response missing access/refresh token")
        raise GoogleOAuthError("Google did not grant calendar access")

    return CalendarTokens(
        user_id=stored["user_id"],
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(body.get("expires_in", 3600)),
        scope=body.get("scope", CALENDAR_SCOPES),
    )
