from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, ForeignKey, Integer, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class BodyMetricsLog(BaseModel):
    """One row per user per day (`uq_metrics_user_date`). A log, not a
    snapshot — see the header of 06_init_body_metrics.sql for how this
    differs from `UserProfile.weight_kg`.

    `measured_bmr_kcal` is a device reading (e.g. InBody), distinct from
    `UserProfile.bmr_kcal`, which is calculated (Mifflin-St Jeor)."""

    __tablename__ = "body_metrics_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    measured_at: Mapped[date] = mapped_column(Date)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    body_fat_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), default=None
    )
    muscle_mass_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    visceral_fat_level: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    measured_bmr_kcal: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 1), default=None
    )
    # Free-form InBody fields not worth a dedicated column (body water %,
    # protein/mineral mass, waist-hip ratio, ...) — never read by SQL, only
    # round-tripped through the API.
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
