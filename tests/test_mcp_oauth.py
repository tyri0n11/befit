"""Tests for the MCP OAuth authorization server (app/mcp/oauth.py, app/mcp/login.py).

Like tests/test_mcp.py's end-to-end test, these talk to the real, committed
database and Redis rather than the rolled-back transaction fixture:
BefitOAuthProvider opens its own `db.session()` per call and its own Redis
client, the same way the MCP tools do, so it never sees
`app.dependency_overrides[get_db]`. Every test here cleans up what it wrote.
"""

import base64
import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator

import httpx2
import pytest_asyncio
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.database import db
from app.core.redis import cache
from app.core.settings import settings
from app.main import app
from app.mcp.oauth import BefitOAuthProvider
from app.models.mcp import OAuthClient
from app.models.user import User

AUTH = f"{settings.API_V1_STR}/auth"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


@pytest_asyncio.fixture
async def rest(connection: AsyncConnection) -> AsyncIterator[httpx2.AsyncClient]:
    """Undependency-overridden client — every call commits for real, on the
    same DB/Redis app/mcp/oauth.py itself talks to. `connection` only
    guarantees db.connect() has already run (it's session-scoped)."""
    cache.connect()  # idempotent
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://oauth-test"
    ) as c:
        yield c


async def test_register_client_persists_and_get_client_round_trips(
    connection: AsyncConnection,  # ensures db.connect() has already run
) -> None:
    provider = BefitOAuthProvider()
    client_id = f"test-{uuid.uuid4().hex}"
    try:
        info = OAuthClientInformationFull(
            client_id=client_id,
            client_secret="s3cret",
            redirect_uris=[AnyUrl("http://127.0.0.1:9999/callback")],
            grant_types=["authorization_code", "refresh_token"],
            token_endpoint_auth_method="client_secret_post",
            client_name="Test Client",
        )
        await provider.register_client(info)

        loaded = await provider.get_client(client_id)
        assert loaded is not None
        assert loaded.client_id == client_id
        assert loaded.client_secret == "s3cret"
        assert str(loaded.redirect_uris[0]) == "http://127.0.0.1:9999/callback"
        assert loaded.client_name == "Test Client"

        assert await provider.get_client("does-not-exist") is None
    finally:
        async with db.session() as session:
            await session.execute(
                delete(OAuthClient).where(OAuthClient.client_id == client_id)
            )


async def test_full_flow_register_authorize_login_token(
    rest: httpx2.AsyncClient,
) -> None:
    metadata = await rest.get("/.well-known/oauth-authorization-server")
    assert metadata.status_code == 200
    assert metadata.json()["token_endpoint"] == f"{settings.MCP_ISSUER_URL}/token"

    register = await rest.post(
        "/register",
        json={
            "redirect_uris": ["http://127.0.0.1:9999/callback"],
            "client_name": "pytest OAuth client",
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert register.status_code == 201, register.text
    client_id = register.json()["client_id"]

    email = f"mcp-oauth-{uuid.uuid4().hex}@example.com"
    password = "correct-horse-battery"

    try:
        registered_user = await rest.post(
            AUTH + "/register",
            json={"email": email, "password": password, "display_name": "OAuth E2E"},
        )
        assert registered_user.status_code == 201, registered_user.text

        verifier, challenge = _pkce_pair()
        authorize = await rest.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "http://127.0.0.1:9999/callback",
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "xyz123",
            },
        )
        assert authorize.status_code == 302
        login_url = authorize.headers["location"]
        assert login_url.startswith(
            f"{settings.MCP_ISSUER_URL}/oauth/login?request_id="
        )
        request_id = login_url.split("request_id=", 1)[1]

        login_page = await rest.get("/oauth/login", params={"request_id": request_id})
        assert login_page.status_code == 200
        assert "pytest OAuth client" in login_page.text

        # Wrong password re-renders the form rather than redirecting.
        bad_login = await rest.post(
            "/oauth/login",
            data={"request_id": request_id, "email": email, "password": "wrong"},
        )
        assert bad_login.status_code == 200
        assert "Incorrect email or password" in bad_login.text

        good_login = await rest.post(
            "/oauth/login",
            data={"request_id": request_id, "email": email, "password": password},
        )
        assert good_login.status_code == 302
        callback_url = good_login.headers["location"]
        assert callback_url.startswith("http://127.0.0.1:9999/callback?")
        assert "state=xyz123" in callback_url
        code = callback_url.split("code=", 1)[1].split("&", 1)[0]

        token = await rest.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://127.0.0.1:9999/callback",
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert token.status_code == 200, token.text
        tokens = token.json()
        assert tokens["token_type"] == "Bearer"
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]

        # The code is single-use: exchanging it again must fail.
        replay = await rest.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://127.0.0.1:9999/callback",
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"

        # The issued access token authenticates against the real REST API too
        # — it's the same JWT app/api/v1/auth/login issues.
        me = await rest.get(
            AUTH + "/me", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert me.status_code == 200
        assert me.json()["email"] == email

        # Refresh grant mints a fresh pair.
        refreshed = await rest.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["access_token"] != access_token
    finally:
        async with db.session() as session:
            await session.execute(delete(User).where(User.email == email))
            await session.execute(
                delete(OAuthClient).where(OAuthClient.client_id == client_id)
            )
