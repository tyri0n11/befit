"""Data access for the 1:1 user profile. The only layer that builds
queries — app/services/user_profile.py must not import sqlalchemy.select."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserProfile


class UserProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, user_id: int) -> UserProfile:
        profile = await self.session.get(UserProfile, user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id)
            self.session.add(profile)
            await self.session.flush()
        return profile

    async def save(self, profile: UserProfile) -> None:
        await self.session.flush()
