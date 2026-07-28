import enum
from datetime import date

from pydantic import BaseModel, Field

from app.models.catalog import MuscleRole
from app.models.training import Bucket


class Period(enum.StrEnum):
    """Preset windows, counted back from today. Weeks are exact; months land on
    the same day-of-month (clamped, so 31 Mar minus one month is 28 Feb)."""

    WEEK_1 = "1w"
    WEEK_2 = "2w"
    WEEK_4 = "4w"
    WEEK_8 = "8w"
    WEEK_12 = "12w"
    MONTH_1 = "1m"
    MONTH_3 = "3m"
    MONTH_6 = "6m"
    YEAR_1 = "1y"


class MuscleRoleFilter(enum.StrEnum):
    """`MuscleRole` plus an explicit "every role". Spelled out rather than
    derived so the OpenAPI schema lists real values — keep it in step with
    `app.models.catalog.MuscleRole`."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    STABILIZER = "stabilizer"
    ALL = "all"

    def to_role(self) -> MuscleRole | None:
        return None if self is MuscleRoleFilter.ALL else MuscleRole(self.value)


class VolumeTotals(BaseModel):
    # Sum of weight x sides x reps. Bodyweight sets contribute 0 — read `reps`
    # alongside it, never on its own.
    tonnage: float
    sets: int
    reps: int
    sessions: int


class VolumePoint(VolumeTotals):
    bucket: date = Field(description="First day of the bucket")


class VolumeReport(BaseModel):
    date_from: date
    date_to: date
    bucket: Bucket
    totals: VolumeTotals
    points: list[VolumePoint]


class MuscleVolume(BaseModel):
    code: str
    name_en: str
    name_vi: str
    tonnage: float
    sets: int
    reps: int


class MuscleVolumeReport(BaseModel):
    date_from: date
    date_to: date
    role: MuscleRole | None
    # These do not sum to the range total — one exercise counts for every muscle
    # it maps to. See `StatsRepository.by_muscle`.
    items: list[MuscleVolume]


class ExerciseVolume(BaseModel):
    exercise_id: int
    slug: str
    name_en: str
    tonnage: float
    sets: int
    reps: int


class ExerciseVolumeReport(BaseModel):
    date_from: date
    date_to: date
    items: list[ExerciseVolume]
