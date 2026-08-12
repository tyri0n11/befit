"""Data access for body metrics logs. The only layer that builds queries —
app/services/body_metrics.py must not import sqlalchemy.select.

Unlike app/repositories/stats.py, aggregation happens in the service, not
here: this is at most one row per user per day, so even years of logging is
a couple thousand rows — nowhere near the volume that rule guards against.
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.body_metrics import BodyMetricsLog


class BodyMetricsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_date(
        self, user_id: int, measured_at: date
    ) -> BodyMetricsLog | None:
        result = await self.session.execute(
            select(BodyMetricsLog).where(
                BodyMetricsLog.user_id == user_id,
                BodyMetricsLog.measured_at == measured_at,
            )
        )
        return result.scalar_one_or_none()

    async def get(self, user_id: int, log_id: int) -> BodyMetricsLog | None:
        result = await self.session.execute(
            select(BodyMetricsLog).where(
                BodyMetricsLog.user_id == user_id, BodyMetricsLog.id == log_id
            )
        )
        return result.scalar_one_or_none()

    async def upsert(self, entry: BodyMetricsLog) -> BodyMetricsLog:
        self.session.add(entry)
        await self.session.flush()
        # updated_at is an onupdate server call; the flush leaves it expired,
        # so re-read it (same pattern app/services/training.py's
        # update_session uses for the same column).
        await self.session.refresh(entry)
        return entry

    async def delete(self, entry: BodyMetricsLog) -> None:
        await self.session.delete(entry)
        await self.session.flush()

    async def list_in_range(
        self, user_id: int, date_from: date, date_to: date
    ) -> list[BodyMetricsLog]:
        result = await self.session.execute(
            select(BodyMetricsLog)
            .where(
                BodyMetricsLog.user_id == user_id,
                BodyMetricsLog.measured_at >= date_from,
                BodyMetricsLog.measured_at <= date_to,
            )
            .order_by(BodyMetricsLog.measured_at.asc())
        )
        return list(result.scalars().all())

    async def list_page(
        self, user_id: int, date_from: date, date_to: date, *, limit: int, offset: int
    ) -> tuple[list[BodyMetricsLog], int]:
        base = select(BodyMetricsLog).where(
            BodyMetricsLog.user_id == user_id,
            BodyMetricsLog.measured_at >= date_from,
            BodyMetricsLog.measured_at <= date_to,
        )
        total = await self.session.scalar(
            select(func.count()).select_from(base.subquery())
        )
        result = await self.session.execute(
            base.order_by(BodyMetricsLog.measured_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all()), total or 0
