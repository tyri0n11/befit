from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.training import ExerciseTargets


class TemplateExerciseCreate(ExerciseTargets):
    exercise_id: int
    order_index: int | None = Field(
        None, ge=1, description="Defaults to the end of the template"
    )
    notes: str | None = None


class TemplateExerciseUpdate(ExerciseTargets):
    order_index: int | None = Field(None, ge=1)
    notes: str | None = None


class TemplateExerciseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exercise_id: int
    slug: str
    name_en: str
    order_index: int
    target_sets: int | None
    target_reps_min: int | None
    target_reps_max: int | None
    target_weight_kg: float | None
    notes: str | None


class WorkoutTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    program_day: str | None = Field(None, max_length=64)
    notes: str | None = None
    exercises: list[TemplateExerciseCreate] = Field(default_factory=list)


class WorkoutTemplateUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    program_day: str | None = Field(None, max_length=64)
    notes: str | None = None


class WorkoutTemplateSummary(BaseModel):
    """List view — a count instead of the nested exercise list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    program_day: str | None
    notes: str | None
    exercise_count: int
    created_at: datetime
    updated_at: datetime


class WorkoutTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    program_day: str | None
    notes: str | None
    exercises: list[TemplateExerciseResponse]
    created_at: datetime
    updated_at: datetime


class WorkoutTemplatePage(BaseModel):
    items: list[WorkoutTemplateSummary]
    total: int
    limit: int
    offset: int


class TemplateFromSession(BaseModel):
    """Turn a session that went well into a reusable plan. Targets are taken
    from the session's own targets, not from what was actually lifted."""

    name: str = Field(min_length=1, max_length=100)
    notes: str | None = None


class TemplateInstantiate(BaseModel):
    """Create a session from a template."""

    session_date: date
    bodyweight_kg: float | None = Field(None, gt=20, lt=300, description="kg")
    notes: str | None = None
