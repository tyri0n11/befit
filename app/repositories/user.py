"""Data access for users and their login methods. The only layer that builds
queries — services must not import sqlalchemy.select themselves."""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import AuthProvider, PasswordResetToken, User, UserAuth


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        # `email` is CITEXT, so this comparison is already case-insensitive.
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        result = await self.session.execute(
            select(User.id).where(User.email == email).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_auth(self, user_id: int, provider: AuthProvider) -> UserAuth | None:
        result = await self.session.execute(
            select(UserAuth).where(
                UserAuth.user_id == user_id, UserAuth.provider == provider
            )
        )
        return result.scalar_one_or_none()

    async def create_local_user(
        self, email: str, display_name: str, password_hash: str
    ) -> User:
        user = User(email=email, display_name=display_name)
        user.auth_methods.append(
            UserAuth(provider=AuthProvider.LOCAL, password_hash=password_hash)
        )
        self.session.add(user)
        # Flush rather than commit: the transaction is owned by db.session().
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def touch_last_login(self, auth: UserAuth) -> None:
        auth.last_login_at = datetime.now(UTC)
        await self.session.flush()

    async def get_auth_by_provider_id(
        self, provider: AuthProvider, provider_user_id: str
    ) -> UserAuth | None:
        result = await self.session.execute(
            select(UserAuth).where(
                UserAuth.provider == provider,
                UserAuth.provider_user_id == provider_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_google_user(
        self, email: str, display_name: str, provider_user_id: str
    ) -> User:
        # email_verified comes straight from Google's assertion — the address is
        # already proven, so there is nothing left for us to verify.
        user = User(email=email, display_name=display_name, email_verified=True)
        user.auth_methods.append(
            UserAuth(provider=AuthProvider.GOOGLE, provider_user_id=provider_user_id)
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def link_google_auth(self, user: User, provider_user_id: str) -> UserAuth:
        """Attach a google login method to an existing (local) account."""
        auth = UserAuth(
            user_id=user.id,
            provider=AuthProvider.GOOGLE,
            provider_user_id=provider_user_id,
        )
        self.session.add(auth)
        # The address is proven by Google even if it was never verified locally.
        user.email_verified = True
        await self.session.flush()
        return auth

    async def create_reset_token(
        self, user_id: int, token_hash: str, expires_at: datetime
    ) -> PasswordResetToken:
        # Any outstanding request for this user is spent first, so only the most
        # recent link works — requesting a new one cancels the old.
        await self.session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=datetime.now(UTC))
        )

        token = PasswordResetToken(
            user_id=user_id, token_hash=token_hash, expires_at=expires_at
        )
        self.session.add(token)
        await self.session.flush()
        return token

    async def get_unused_reset_token(
        self, token_hash: str
    ) -> PasswordResetToken | None:
        result = await self.session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > datetime.now(UTC),
            )
        )
        return result.scalar_one_or_none()

    async def apply_password_reset(
        self, token: PasswordResetToken, user: User, password_hash: str, auth: UserAuth
    ) -> None:
        """Consume the token, set the new hash, and revoke existing sessions."""
        token.used_at = datetime.now(UTC)
        auth.password_hash = password_hash
        # Invalidates every access and refresh token already issued to this user.
        user.token_version += 1
        await self.session.flush()
