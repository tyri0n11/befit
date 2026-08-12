"""Physical profile, 1:1 with the user. `bmr_kcal`/`tdee_kcal` are derived
(Mifflin-St Jeor) here, recomputed on every update rather than being fields
a client can set directly — see `UserProfileResponse`.

A profile row is created lazily on first read/write rather than at
registration: most users never fill it in, and every column is nullable, so
there is nothing a fresh row would carry that `get_or_create` doesn't
already produce on demand.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Sex, UserProfile
from app.repositories.user_profile import UserProfileRepository
from app.schemas.user import UserProfileUpdate

# 1 sedentary, 2 lightly active, 3 moderately active, 4 very active,
# 5 extra active — the standard Mifflin-St Jeor activity multipliers.
_ACTIVITY_MULTIPLIER: dict[int, Decimal] = {
    1: Decimal("1.2"),
    2: Decimal("1.375"),
    3: Decimal("1.55"),
    4: Decimal("1.725"),
    5: Decimal("1.9"),
}

_ONE_DP = Decimal("0.1")


def _dec(value: float | None) -> Decimal | None:
    """The column is NUMERIC; going through `str` avoids the binary-float
    artefacts `Decimal(70.5)` would otherwise store (see app/services/
    template.py's `_dec`, the same helper for the same reason)."""
    return None if value is None else Decimal(str(value))


def _age(birth_date: date, *, today: date | None = None) -> int:
    today = today or date.today()
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def _bmr(profile: UserProfile) -> Decimal | None:
    if not (
        profile.sex and profile.weight_kg and profile.height_cm and profile.birth_date
    ):
        return None
    base = (
        Decimal(10) * profile.weight_kg
        + Decimal("6.25") * profile.height_cm
        - Decimal(5) * _age(profile.birth_date)
    )
    signed = base + 5 if profile.sex is Sex.MALE else base - 161
    return signed.quantize(_ONE_DP, rounding=ROUND_HALF_UP)


class UserProfileService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = UserProfileRepository(session)

    async def get(self, user_id: int) -> UserProfile:
        return await self.repo.get_or_create(user_id)

    async def update(self, user_id: int, payload: UserProfileUpdate) -> UserProfile:
        profile = await self.repo.get_or_create(user_id)
        fields = payload.model_dump(exclude_unset=True)
        if "weight_kg" in fields:
            fields["weight_kg"] = _dec(fields["weight_kg"])
        for field, value in fields.items():
            setattr(profile, field, value)

        profile.bmr_kcal = bmr = _bmr(profile)
        profile.tdee_kcal = (
            (bmr * _ACTIVITY_MULTIPLIER[profile.activity_level]).quantize(
                _ONE_DP, rounding=ROUND_HALF_UP
            )
            if bmr is not None and profile.activity_level
            else None
        )

        await self.repo.save(profile)
        return profile
