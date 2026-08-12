from datetime import date, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Mirrors the CHECK constraints in scripts/database/06_init_body_metrics.sql,
# so a bad payload is a 422 instead of an IntegrityError.
WeightKg = Field(None, gt=20, lt=300)
BodyFatPercent = Field(None, ge=0, le=70)
MuscleMassKg = Field(None, gt=0, lt=200)
VisceralFatLevel = Field(None, gt=0, lt=60)
MeasuredBmrKcal = Field(
    None, gt=500, lt=5000, description="Device reading (e.g. InBody)"
)

_METRIC_FIELDS = (
    "weight_kg",
    "body_fat_percent",
    "muscle_mass_kg",
    "visceral_fat_level",
    "measured_bmr_kcal",
)


class BodyMetricsLogCreate(BaseModel):
    measured_at: date = Field(default_factory=date.today)
    weight_kg: float | None = WeightKg
    body_fat_percent: float | None = BodyFatPercent
    muscle_mass_kg: float | None = MuscleMassKg
    visceral_fat_level: int | None = VisceralFatLevel
    measured_bmr_kcal: float | None = MeasuredBmrKcal
    # Free-form InBody fields not worth a dedicated column (body water %,
    # protein/mineral mass, waist-hip ratio, ...). Stored as-is, never
    # validated or read by SQL.
    extra: dict[str, Any] | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _has_at_least_one_metric(self) -> Self:
        if all(getattr(self, field) is None for field in _METRIC_FIELDS):
            raise ValueError(
                "at least one of " + ", ".join(_METRIC_FIELDS) + " is required"
            )
        return self


class BodyMetricsLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    measured_at: date
    weight_kg: float | None
    body_fat_percent: float | None
    muscle_mass_kg: float | None
    visceral_fat_level: int | None
    measured_bmr_kcal: float | None
    extra: dict[str, Any] | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class BodyMetricsLogPage(BaseModel):
    items: list[BodyMetricsLogResponse]
    total: int
    limit: int
    offset: int


class MetricProgress(BaseModel):
    """`current` is the most recent value on or before `date_to`; `change` is
    `current - baseline`, null whenever either side is missing (the metric
    was never logged, or wasn't logged again inside the window)."""

    baseline: float | None
    current: float | None
    change: float | None


class BodyMetricsProgress(BaseModel):
    date_from: date
    date_to: date
    weight_kg: MetricProgress
    body_fat_percent: MetricProgress
    muscle_mass_kg: MetricProgress
    visceral_fat_level: MetricProgress
    measured_bmr_kcal: MetricProgress
