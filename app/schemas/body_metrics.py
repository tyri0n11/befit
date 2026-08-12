from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Mirrors the CHECK constraints in scripts/database/06_init_body_metrics.sql,
# so a bad payload is a 422 instead of an IntegrityError.
WeightKg = Field(None, gt=20, lt=300)
BodyFatPercent = Field(None, ge=0, le=70)
MuscleMassKg = Field(None, gt=0, lt=200)


class BodyMetricsLogCreate(BaseModel):
    measured_at: date = Field(default_factory=date.today)
    weight_kg: float | None = WeightKg
    body_fat_percent: float | None = BodyFatPercent
    muscle_mass_kg: float | None = MuscleMassKg
    notes: str | None = None

    @model_validator(mode="after")
    def _has_at_least_one_metric(self) -> Self:
        if self.weight_kg is self.body_fat_percent is self.muscle_mass_kg is None:
            raise ValueError(
                "at least one of weight_kg, body_fat_percent, "
                "muscle_mass_kg is required"
            )
        return self


class BodyMetricsLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    measured_at: date
    weight_kg: float | None
    body_fat_percent: float | None
    muscle_mass_kg: float | None
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
