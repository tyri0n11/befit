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

_STATE_PREFIX = "oauth:google:state:"
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


async def _exchange_code(code: str, verifier: str) -> str:
    """Swap the authorization code for an id_token."""
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            },
        )

    if response.is_error:
        # Google's body names the misconfiguration (redirect_uri_mismatch,
        # invalid_client, ...), which is worth having in the log.
        logger.error(
            "google token exchange failed: %s %s", response.status_code, response.text
        )
        raise GoogleOAuthError("Google rejected the sign-in attempt")

    id_token = response.json().get("id_token")
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
