from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class BodyMetricsLog(BaseModel):
    """One row per user per day (`uq_metrics_user_date`). A log, not a
    snapshot — see the header of 06_init_body_metrics.sql for how this
    differs from `UserProfile.weight_kg`."""

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
    notes: Mapped[str | None] = mapped_column(Text, default=None)
