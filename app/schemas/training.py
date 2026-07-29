from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.training import ExerciseStatus, SessionStatus

# Mirrors the CHECK constraints in scripts/database/03_init_training.sql, so a
# bad payload is a 422 instead of an IntegrityError.
Bodyweight = Field(None, gt=20, lt=300, description="kg")
Weight = Field(None, ge=0, description="kg, null for bodyweight movements")
Rpe = Field(None, ge=5, le=10)
Rest = Field(None, ge=0, description="seconds")


class SetLogCreate(BaseModel):
    reps: int = Field(ge=1, le=1000)
    weight_kg: float | None = Weight
    rpe: float | None = Rpe
    rest_before_sec: int | None = Rest
    rom_note: str | None = None
    set_index: int | None = Field(
        None, ge=1, description="Defaults to the next index for this exercise"
    )


class SetLogUpdate(BaseModel):
    reps: int | None = Field(None, ge=1, le=1000)
    weight_kg: float | None = Weight
    rpe: float | None = Rpe
    rest_before_sec: int | None = Rest
    rom_note: str | None = None


class SetLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    set_index: int
    reps: int
    weight_kg: float | None
    rpe: float | None
    rest_before_sec: int | None
    rom_note: str | None
    logged_at: datetime


class ExerciseTargets(BaseModel):
    """Shared by sessions and templates — the reps-order rule must not drift
    between the two, and it mirrors `ck_target_reps_order` in SQL."""

    target_sets: int | None = Field(None, ge=1, le=100)
    target_reps_min: int | None = Field(None, ge=1, le=1000)
    target_reps_max: int | None = Field(None, ge=1, le=1000)
    target_weight_kg: float | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _reps_ordered(self) -> Self:
        lo, hi = self.target_reps_min, self.target_reps_max
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("target_reps_min must not exceed target_reps_max")
        return self


class SessionExerciseCreate(ExerciseTargets):
    exercise_id: int
    order_index: int | None = Field(
        None, ge=1, description="Defaults to the end of the session"
    )
    notes: str | None = None


class SessionExerciseUpdate(ExerciseTargets):
    status: ExerciseStatus | None = None
    order_index: int | None = Field(None, ge=1)
    skip_reason: int | None = None
    notes: str | None = None


class SessionExerciseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exercise_id: int
    slug: str
    name_en: str
    order_index: int
    status: ExerciseStatus
    target_sets: int | None
    target_reps_min: int | None
    target_reps_max: int | None
    target_weight_kg: float | None
    skip_reason: int | None
    notes: str | None
    tonnage: float
    sets: list[SetLogResponse]


class WorkoutSessionCreate(BaseModel):
    session_date: date
    status: SessionStatus = SessionStatus.PLANNED
    bodyweight_kg: float | None = Bodyweight
    program_day: str | None = Field(None, max_length=64)
    notes: str | None = None
    # Planning a whole day in one call is the common case; order_index is
    # assigned from the list position when omitted.
    exercises: list[SessionExerciseCreate] = Field(default_factory=list)


class WorkoutSessionUpdate(BaseModel):
    session_date: date | None = None
    status: SessionStatus | None = None
    bodyweight_kg: float | None = Bodyweight
    program_day: str | None = Field(None, max_length=64)
    notes: str | None = None


class WorkoutSessionSummary(BaseModel):
    """List view — counts and tonnage instead of the nested exercise graph."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_date: date
    status: SessionStatus
    bodyweight_kg: float | None
    program_day: str | None
    notes: str | None
    exercise_count: int
    set_count: int
    tonnage: float
    # Non-null only when the caller asked for deleted rows; live listings
    # filter them out entirely.
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkoutSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_date: date
    status: SessionStatus
    bodyweight_kg: float | None
    program_day: str | None
    notes: str | None
    tonnage: float
    exercises: list[SessionExerciseResponse]
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkoutSessionPage(BaseModel):
    items: list[WorkoutSessionSummary]
    total: int
    limit: int
    offset: int
