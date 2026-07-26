"""Smoke tests for the auth endpoints. Nothing persists — see conftest.py."""

from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import RecordingEmailSender

from app.core.settings import settings
from app.models.user import User, UserAuth
from app.services.auth import AuthError, AuthErrorCode, AuthService
from app.services.google_oauth import (
    AUTHORIZE_URL,
    GoogleIdentity,
    GoogleOAuthError,
)
from app.services.google_oauth import _consume_state as consume_state

BASE = f"{settings.API_V1_STR}/auth"

CREDENTIALS = {
    "email": "smoke@example.com",
    "display_name": "Smoke",
    "password": "correct-horse-battery",
}
LOGIN = {"email": CREDENTIALS["email"], "password": CREDENTIALS["password"]}


async def register(client: AsyncClient, **overrides: object) -> object:
    return await client.post(BASE + "/register", json={**CREDENTIALS, **overrides})


async def _fetch_last_login(session: AsyncSession) -> datetime | None:
    """Read `last_login_at` for the smoke user through the test's own session,
    which is the only place the uncommitted row is visible."""
    result = await session.execute(
        select(UserAuth.last_login_at)
        .join(User, User.id == UserAuth.user_id)
        .where(User.email == CREDENTIALS["email"])
    )
    return result.scalar_one()


async def _request_reset(
    client: AsyncClient,
    mailer: RecordingEmailSender,
    register_first: bool = True,
) -> str:
    """Trigger a reset and return the raw token the mailer captured."""
    if register_first:
        await register(client)
    response = await client.post(
        BASE + "/password-reset/request", json={"email": CREDENTIALS["email"]}
    )
    assert response.status_code == 202, response.text
    return mailer.sent[-1][1]


async def _fetch_user(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def login_tokens(client: AsyncClient) -> tuple[str, str]:
    await register(client)
    response = await client.post(BASE + "/login", json=LOGIN)
    assert response.status_code == 200, response.text
    body = response.json()
    return body["access_token"], body["refresh_token"]


class TestRegister:
    async def test_creates_user(self, client: AsyncClient) -> None:
        response = await register(client)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["email"] == CREDENTIALS["email"]
        assert body["display_name"] == "Smoke"
        assert body["status"] == "active"
        assert body["email_verified"] is False
        assert "password" not in body
        assert "password_hash" not in body

    async def test_duplicate_email_conflicts(self, client: AsyncClient) -> None:
        await register(client)
        response = await register(client, display_name="Other")

        assert response.status_code == 409

    async def test_duplicate_is_case_insensitive(self, client: AsyncClient) -> None:
        """`users.email` is CITEXT, so casing must not open a second account."""
        await register(client)
        response = await register(client, email="SMOKE@EXAMPLE.COM")

        assert response.status_code == 409

    async def test_rejects_short_password(self, client: AsyncClient) -> None:
        assert (await register(client, password="short")).status_code == 422

    async def test_rejects_password_over_bcrypt_limit(
        self, client: AsyncClient
    ) -> None:
        """bcrypt truncates at 72 bytes; a longer password must be refused
        rather than silently equal its own prefix."""
        assert (await register(client, password="a" * 73)).status_code == 422

    async def test_rejects_malformed_email(self, client: AsyncClient) -> None:
        assert (await register(client, email="not-an-email")).status_code == 422

    async def test_rejects_empty_display_name(self, client: AsyncClient) -> None:
        assert (await register(client, display_name="")).status_code == 422


class TestLogin:
    async def test_returns_token_pair(self, client: AsyncClient) -> None:
        await register(client)
        response = await client.post(BASE + "/login", json=LOGIN)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"] != body["refresh_token"]

    async def test_email_is_case_insensitive(self, client: AsyncClient) -> None:
        await register(client)
        response = await client.post(
            BASE + "/login", json={**LOGIN, "email": "SMOKE@EXAMPLE.COM"}
        )

        assert response.status_code == 200, response.text

    async def test_wrong_password_and_unknown_email_are_indistinguishable(
        self, client: AsyncClient
    ) -> None:
        """Otherwise the endpoint enumerates which emails are registered."""
        await register(client)
        wrong_password = await client.post(
            BASE + "/login", json={**LOGIN, "password": "wrong-password"}
        )
        unknown_email = await client.post(
            BASE + "/login", json={"email": "ghost@example.com", "password": "wrong"}
        )

        assert wrong_password.status_code == unknown_email.status_code == 401
        assert wrong_password.json() == unknown_email.json()

    async def test_records_last_login(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await register(client)
        before = await _fetch_last_login(session)
        assert before is None

        await client.post(BASE + "/login", json=LOGIN)

        assert await _fetch_last_login(session) is not None


class TestMe:
    async def test_returns_current_user(self, client: AsyncClient) -> None:
        access, _ = await login_tokens(client)
        response = await client.get(
            BASE + "/me", headers={"Authorization": f"Bearer {access}"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["email"] == CREDENTIALS["email"]

    async def test_requires_a_token(self, client: AsyncClient) -> None:
        assert (await client.get(BASE + "/me")).status_code == 401

    async def test_rejects_refresh_token(self, client: AsyncClient) -> None:
        """A refresh token is long-lived; it must not work as a bearer token."""
        _, refresh = await login_tokens(client)
        response = await client.get(
            BASE + "/me", headers={"Authorization": f"Bearer {refresh}"}
        )

        assert response.status_code == 401

    async def test_rejects_garbage_token(self, client: AsyncClient) -> None:
        response = await client.get(
            BASE + "/me", headers={"Authorization": "Bearer aa.bb.cc"}
        )

        assert response.status_code == 401
        # The message must not leak decoder internals.
        assert response.json()["detail"] == "Invalid or expired token"


class TestRefresh:
    async def test_issues_a_new_pair(self, client: AsyncClient) -> None:
        _, refresh = await login_tokens(client)
        response = await client.post(BASE + "/refresh", json={"refresh_token": refresh})

        assert response.status_code == 200, response.text
        assert response.json()["access_token"]

    async def test_rejects_access_token(self, client: AsyncClient) -> None:
        access, _ = await login_tokens(client)
        response = await client.post(BASE + "/refresh", json={"refresh_token": access})

        assert response.status_code == 401

    async def test_rejects_garbage(self, client: AsyncClient) -> None:
        response = await client.post(
            BASE + "/refresh", json={"refresh_token": "nonsense"}
        )

        assert response.status_code == 401


class TestPasswordReset:
    async def test_request_returns_202_for_unknown_email(
        self, client: AsyncClient
    ) -> None:
        """Same response as a known address, or the endpoint enumerates accounts."""
        response = await client.post(
            BASE + "/password-reset/request", json={"email": "ghost@example.com"}
        )

        assert response.status_code == 202

    async def test_request_returns_202_for_known_email(
        self, client: AsyncClient, mailer: RecordingEmailSender
    ) -> None:
        await register(client)
        response = await client.post(
            BASE + "/password-reset/request", json={"email": CREDENTIALS["email"]}
        )

        assert response.status_code == 202
        assert len(mailer.sent) == 1
        assert mailer.sent[0][0] == CREDENTIALS["email"]

    async def test_no_email_sent_for_unknown_address(
        self, client: AsyncClient, mailer: RecordingEmailSender
    ) -> None:
        await client.post(
            BASE + "/password-reset/request", json={"email": "ghost@example.com"}
        )

        assert mailer.sent == []

    async def test_confirm_sets_the_new_password(
        self, client: AsyncClient, mailer: RecordingEmailSender
    ) -> None:
        token = await _request_reset(client, mailer)

        confirm = await client.post(
            BASE + "/password-reset/confirm",
            json={"token": token, "new_password": "brand-new-password"},
        )
        assert confirm.status_code == 204, confirm.text

        old = await client.post(BASE + "/login", json=LOGIN)
        assert old.status_code == 401

        new = await client.post(
            BASE + "/login",
            json={"email": CREDENTIALS["email"], "password": "brand-new-password"},
        )
        assert new.status_code == 200, new.text

    async def test_token_is_single_use(
        self, client: AsyncClient, mailer: RecordingEmailSender
    ) -> None:
        token = await _request_reset(client, mailer)
        body = {"token": token, "new_password": "brand-new-password"}

        first = await client.post(BASE + "/password-reset/confirm", json=body)
        assert first.status_code == 204, first.text

        second = await client.post(BASE + "/password-reset/confirm", json=body)

        assert second.status_code == 400

    async def test_requesting_again_invalidates_the_previous_token(
        self, client: AsyncClient, mailer: RecordingEmailSender
    ) -> None:
        first = await _request_reset(client, mailer)
        second = await _request_reset(client, mailer, register_first=False)
        assert first != second

        stale = await client.post(
            BASE + "/password-reset/confirm",
            json={"token": first, "new_password": "brand-new-password"},
        )
        assert stale.status_code == 400

        fresh = await client.post(
            BASE + "/password-reset/confirm",
            json={"token": second, "new_password": "brand-new-password"},
        )
        assert fresh.status_code == 204, fresh.text

    async def test_reset_revokes_existing_tokens(
        self, client: AsyncClient, mailer: RecordingEmailSender
    ) -> None:
        """A stolen refresh token must stop working once the victim resets."""
        access, refresh = await login_tokens(client)
        assert (
            await client.get(
                BASE + "/me", headers={"Authorization": f"Bearer {access}"}
            )
        ).status_code == 200

        token = await _request_reset(client, mailer, register_first=False)
        await client.post(
            BASE + "/password-reset/confirm",
            json={"token": token, "new_password": "brand-new-password"},
        )

        assert (
            await client.get(
                BASE + "/me", headers={"Authorization": f"Bearer {access}"}
            )
        ).status_code == 401
        assert (
            await client.post(BASE + "/refresh", json={"refresh_token": refresh})
        ).status_code == 401

    async def test_confirm_rejects_garbage_token(self, client: AsyncClient) -> None:
        response = await client.post(
            BASE + "/password-reset/confirm",
            json={"token": "nonsense", "new_password": "brand-new-password"},
        )

        assert response.status_code == 400

    async def test_confirm_rejects_short_password(
        self, client: AsyncClient, mailer: RecordingEmailSender
    ) -> None:
        token = await _request_reset(client, mailer)
        response = await client.post(
            BASE + "/password-reset/confirm",
            json={"token": token, "new_password": "short"},
        )

        assert response.status_code == 422


class TestGoogleOAuth:
    async def test_login_redirect_requires_configuration(
        self, client: AsyncClient
    ) -> None:
        """With no client id/secret the endpoint must say so, not 500."""
        response = await client.get(BASE + "/google/login", follow_redirects=False)

        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]

    async def test_login_redirects_to_google(
        self, client: AsyncClient, google_configured: None
    ) -> None:
        response = await client.get(BASE + "/google/login", follow_redirects=False)

        assert response.status_code == 307
        location = response.headers["location"]
        query = parse_qs(urlparse(location).query)
        assert location.startswith(AUTHORIZE_URL)
        assert query["client_id"] == ["test-client-id"]
        assert query["response_type"] == ["code"]
        # PKCE and a state parameter are both required, not optional extras.
        assert query["code_challenge_method"] == ["S256"]
        assert query["code_challenge"] and query["state"]

    async def test_callback_reports_user_cancellation(
        self, client: AsyncClient
    ) -> None:
        response = await client.get(BASE + "/google/callback?error=access_denied")

        assert response.status_code == 400
        assert "access_denied" in response.json()["detail"]

    async def test_callback_requires_code_and_state(self, client: AsyncClient) -> None:
        response = await client.get(BASE + "/google/callback?code=abc")

        assert response.status_code == 400

    async def test_callback_rejects_unknown_state(self, client: AsyncClient) -> None:
        """State must be present in Redis; a forged one cannot be accepted."""
        response = await client.get(
            BASE + "/google/callback?code=abc&state=never-issued"
        )

        assert response.status_code == 400
        assert "expired or was already used" in response.json()["detail"]

    async def test_state_is_single_use(
        self, client: AsyncClient, google_configured: None, fake_redis
    ) -> None:
        """Otherwise a captured callback URL could be replayed."""
        redirect = await client.get(BASE + "/google/login", follow_redirects=False)
        state = parse_qs(urlparse(redirect.headers["location"]).query)["state"][0]

        assert await fake_redis.get(f"oauth:google:state:{state}") is not None
        await consume_state(fake_redis, state)
        assert await fake_redis.get(f"oauth:google:state:{state}") is None

        with pytest.raises(GoogleOAuthError):
            await consume_state(fake_redis, state)


class TestGoogleIdentityLogin:
    """login_with_google works off a verified identity, so these tests inject one
    directly rather than mocking Google's endpoints."""

    @staticmethod
    def identity(**overrides: object) -> GoogleIdentity:
        base = {
            "subject": "google-sub-1057",
            "email": "gmail-user@example.com",
            "email_verified": True,
            "display_name": "Gmail User",
        }
        return GoogleIdentity(**{**base, **overrides})  # type: ignore[arg-type]

    async def test_creates_a_new_user(
        self, session: AsyncSession, mailer: RecordingEmailSender
    ) -> None:
        service = AuthService(session, mailer)
        tokens = await service.login_with_google(self.identity())

        assert tokens.access_token
        user = await _fetch_user(session, "gmail-user@example.com")
        assert user is not None
        # Google already proved the address.
        assert user.email_verified is True

    async def test_second_login_reuses_the_same_user(
        self, session: AsyncSession, mailer: RecordingEmailSender
    ) -> None:
        service = AuthService(session, mailer)
        await service.login_with_google(self.identity())
        await service.login_with_google(self.identity())

        result = await session.execute(
            select(User).where(User.email == "gmail-user@example.com")
        )
        assert len(result.scalars().all()) == 1

    async def test_links_to_an_existing_local_account(
        self, client: AsyncClient, session: AsyncSession, mailer: RecordingEmailSender
    ) -> None:
        """Same email as a password account: link, do not create a duplicate."""
        await register(client)
        service = AuthService(session, mailer)

        await service.login_with_google(self.identity(email=CREDENTIALS["email"]))

        user = await _fetch_user(session, CREDENTIALS["email"])
        assert user is not None
        providers = {a.provider.value for a in user.auth_methods}
        assert providers == {"local", "google"}
        # Password login must keep working after linking.
        assert (await client.post(BASE + "/login", json=LOGIN)).status_code == 200

    async def test_refuses_to_link_an_unverified_email(
        self, client: AsyncClient, session: AsyncSession, mailer: RecordingEmailSender
    ) -> None:
        """Otherwise a Google account claiming someone else's address takes over
        their local account."""
        await register(client)
        service = AuthService(session, mailer)

        with pytest.raises(AuthError) as caught:
            await service.login_with_google(
                self.identity(email=CREDENTIALS["email"], email_verified=False)
            )

        assert caught.value.code is AuthErrorCode.EMAIL_NOT_VERIFIED
