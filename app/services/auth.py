"""Authentication business logic. Raises AuthError; the endpoint layer maps it
to HTTP status codes. Never commits — db.session() owns the transaction."""

import enum
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.models.user import AuthProvider, User, UserStatus
from app.repositories.user import UserRepository
from app.schemas.auth import TokenPair
from app.services.email import EmailSender
from app.services.google_oauth import GoogleIdentity
from app.utils.security import (
    TokenError,
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_reset_token,
    hash_password,
    hash_reset_token,
    verify_password,
)


class AuthErrorCode(enum.StrEnum):
    EMAIL_TAKEN = "EMAIL_TAKEN"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"
    INVALID_TOKEN = "INVALID_TOKEN"
    INVALID_RESET_TOKEN = "INVALID_RESET_TOKEN"
    OAUTH_FAILED = "OAUTH_FAILED"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"


_INVALID_TOKEN = "Invalid or expired token"
_INVALID_RESET_TOKEN = "This reset link is invalid, expired, or already used"


class AuthError(Exception):
    def __init__(self, code: AuthErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AuthService:
    def __init__(self, session: AsyncSession, email: EmailSender) -> None:
        self.users = UserRepository(session)
        self.email = email

    @staticmethod
    def _tokens(user: User) -> TokenPair:
        return TokenPair(
            access_token=create_access_token(user.id, user.token_version),
            refresh_token=create_refresh_token(user.id, user.token_version),
        )

    async def register(self, email: str, display_name: str, password: str) -> User:
        if await self.users.email_exists(email):
            raise AuthError(
                AuthErrorCode.EMAIL_TAKEN, "That email is already registered"
            )

        return await self.users.create_local_user(
            email=email,
            display_name=display_name,
            password_hash=hash_password(password),
        )

    async def login(self, email: str, password: str) -> TokenPair:
        user = await self.users.get_by_email(email)

        # Same error whether the email is unknown or the password is wrong, so the
        # response cannot be used to enumerate registered accounts.
        invalid = AuthError(
            AuthErrorCode.INVALID_CREDENTIALS, "Incorrect email or password"
        )
        if user is None:
            raise invalid

        auth = await self.users.get_auth(user.id, AuthProvider.LOCAL)
        if auth is None or auth.password_hash is None:
            raise invalid
        if not verify_password(password, auth.password_hash):
            raise invalid

        # Checked after the password so a suspended account still cannot be
        # probed without valid credentials.
        if user.status is not UserStatus.ACTIVE:
            raise AuthError(
                AuthErrorCode.ACCOUNT_INACTIVE,
                f"This account is {user.status.value}",
            )

        await self.users.touch_last_login(auth)
        return self._tokens(user)

    async def refresh(self, refresh_token: str) -> TokenPair:
        try:
            claims = decode_token(refresh_token, "refresh")
        except TokenError as exc:
            # Deliberately generic: the underlying TokenError text can expose
            # decoder internals, and which token type was supplied is not the
            # caller's business. The cause stays chained for the logs.
            raise AuthError(AuthErrorCode.INVALID_TOKEN, _INVALID_TOKEN) from exc

        user = await self._active_user_for(claims)
        return self._tokens(user)

    async def user_from_access_token(self, token: str) -> User:
        try:
            claims = decode_token(token, "access")
        except TokenError as exc:
            # Deliberately generic: the underlying TokenError text can expose
            # decoder internals, and which token type was supplied is not the
            # caller's business. The cause stays chained for the logs.
            raise AuthError(AuthErrorCode.INVALID_TOKEN, _INVALID_TOKEN) from exc

        return await self._active_user_for(claims)

    async def _active_user_for(self, claims: TokenPayload) -> User:
        user = await self.users.get_by_id(claims.user_id)
        invalid = AuthError(AuthErrorCode.INVALID_TOKEN, _INVALID_TOKEN)
        if user is None or user.status is not UserStatus.ACTIVE:
            raise invalid
        # A password reset bumps token_version, so anything signed before it is
        # refused here — this is what makes reset revoke existing sessions.
        if claims.token_version != user.token_version:
            raise invalid
        return user

    async def login_with_google(self, identity: GoogleIdentity) -> TokenPair:
        """Sign in (or sign up) using a verified Google identity.

        Three cases: a known google subject, an existing local account with the
        same email, or a brand new user.
        """
        auth = await self.users.get_auth_by_provider_id(
            AuthProvider.GOOGLE, identity.subject
        )
        if auth is not None:
            user = await self.users.get_by_id(auth.user_id)
            if user is None or user.status is not UserStatus.ACTIVE:
                raise AuthError(
                    AuthErrorCode.ACCOUNT_INACTIVE, "This account is not active"
                )
            await self.users.touch_last_login(auth)
            return self._tokens(user)

        # Linking on email is only safe if Google asserts the address is proven.
        # Otherwise anyone could create a Google account claiming someone else's
        # address and take over their local account.
        if not identity.email_verified:
            raise AuthError(
                AuthErrorCode.EMAIL_NOT_VERIFIED,
                "Google has not verified this email address",
            )

        existing = await self.users.get_by_email(identity.email)
        if existing is not None:
            if existing.status is not UserStatus.ACTIVE:
                raise AuthError(
                    AuthErrorCode.ACCOUNT_INACTIVE, "This account is not active"
                )
            linked = await self.users.link_google_auth(existing, identity.subject)
            await self.users.touch_last_login(linked)
            return self._tokens(existing)

        user = await self.users.create_google_user(
            email=identity.email,
            display_name=identity.display_name,
            provider_user_id=identity.subject,
        )
        return self._tokens(user)

    async def request_password_reset(self, email: str) -> None:
        """Always returns normally, whether or not the address is registered —
        a distinguishable response would leak which emails have accounts."""
        user = await self.users.get_by_email(email)
        if user is None or user.status is not UserStatus.ACTIVE:
            return

        auth = await self.users.get_auth(user.id, AuthProvider.LOCAL)
        if auth is None:
            # A google-only account has no password to reset.
            return

        token = generate_reset_token()
        await self.users.create_reset_token(
            user_id=user.id,
            token_hash=hash_reset_token(token),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        )
        await self.email.send_password_reset(user.email, token)

    async def confirm_password_reset(self, token: str, new_password: str) -> None:
        reset = await self.users.get_unused_reset_token(hash_reset_token(token))
        if reset is None:
            raise AuthError(AuthErrorCode.INVALID_RESET_TOKEN, _INVALID_RESET_TOKEN)

        user = await self.users.get_by_id(reset.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            raise AuthError(AuthErrorCode.INVALID_RESET_TOKEN, _INVALID_RESET_TOKEN)

        auth = await self.users.get_auth(user.id, AuthProvider.LOCAL)
        if auth is None:
            raise AuthError(AuthErrorCode.INVALID_RESET_TOKEN, _INVALID_RESET_TOKEN)

        await self.users.apply_password_reset(
            token=reset,
            user=user,
            password_hash=hash_password(new_password),
            auth=auth,
        )
