from datetime import date
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.user import Sex, TrainingGoal

# Mirrors the CHECK constraints in scripts/database/01_init_user.sql, so a
# bad payload is a 422 instead of an IntegrityError.
HeightCm = Field(None, gt=80, lt=260)
WeightKg = Field(None, gt=20, lt=300)
ActivityLevel = Field(None, ge=1, le=5, description="1 (sedentary) .. 5 (extra active)")


class UserProfileUpdate(BaseModel):
    sex: Sex | None = None
    birth_date: date | None = None
    height_cm: int | None = HeightCm
    weight_kg: float | None = WeightKg
    goal: TrainingGoal | None = None
    activity_level: int | None = ActivityLevel

    @model_validator(mode="after")
    def _birth_date_in_past(self) -> Self:
        if self.birth_date is not None and self.birth_date >= date.today():
            raise ValueError("birth_date must be in the past")
        return self


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sex: Sex | None
    birth_date: date | None
    height_cm: int | None
    weight_kg: float | None
    goal: TrainingGoal | None
    activity_level: int | None
    # Derived (Mifflin-St Jeor) — null until sex, birth_date, height_cm and
    # weight_kg are all present; tdee_kcal additionally needs activity_level.
    bmr_kcal: float | None
    tdee_kcal: float | None
