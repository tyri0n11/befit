"""Body metrics log — weight/body-fat/muscle-mass over time.

One row per user per day: logging again for a day you already logged
updates that row in place rather than adding a second one (see
`uq_metrics_user_date` in 06_init_body_metrics.sql). Progress is computed
in Python, not SQL, deliberately unlike app/repositories/stats.py — this is
at most a few thousand rows per user even after years of daily logging, well
short of the volume that rule guards against.
"""

import enum
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.body_metrics import BodyMetricsLog
from app.repositories.body_metrics import BodyMetricsRepository
from app.repositories.stats import StatsRange
from app.schemas.body_metrics import (
    BodyMetricsLogCreate,
    BodyMetricsProgress,
    MetricProgress,
)


class BodyMetricsErrorCode(enum.StrEnum):
    LOG_NOT_FOUND = "log_not_found"


class BodyMetricsError(Exception):
    def __init__(self, code: BodyMetricsErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _dec(value: float | None) -> Decimal | None:
    """The columns are NUMERIC; going through `str` avoids the binary-float
    artefacts `Decimal(70.5)` would otherwise store (see app/services/
    template.py's `_dec`, the same helper for the same reason)."""
    return None if value is None else Decimal(str(value))


def _metric_progress(entries: list[BodyMetricsLog], field: str) -> MetricProgress:
    values = [
        (entry.measured_at, getattr(entry, field))
        for entry in entries
        if getattr(entry, field) is not None
    ]
    if not values:
        return MetricProgress(baseline=None, current=None, change=None)
    baseline = float(values[0][1])
    current = float(values[-1][1])
    return MetricProgress(baseline=baseline, current=current, change=current - baseline)


class BodyMetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = BodyMetricsRepository(session)

    async def log(self, user_id: int, payload: BodyMetricsLogCreate) -> BodyMetricsLog:
        """Create or, for a day already logged, overwrite it — this is a full
        replacement of that day's entry, not a partial patch."""
        entry = await self.repo.get_for_date(user_id, payload.measured_at)
        if entry is None:
            entry = BodyMetricsLog(user_id=user_id, measured_at=payload.measured_at)
        entry.weight_kg = _dec(payload.weight_kg)
        entry.body_fat_percent = _dec(payload.body_fat_percent)
        entry.muscle_mass_kg = _dec(payload.muscle_mass_kg)
        entry.visceral_fat_level = payload.visceral_fat_level
        entry.measured_bmr_kcal = _dec(payload.measured_bmr_kcal)
        entry.extra = payload.extra
        entry.notes = payload.notes
        return await self.repo.upsert(entry)

    async def list_page(
        self, user_id: int, window: StatsRange, *, limit: int, offset: int
    ) -> tuple[list[BodyMetricsLog], int]:
        return await self.repo.list_page(
            user_id, window.date_from, window.date_to, limit=limit, offset=offset
        )

    async def progress(self, user_id: int, window: StatsRange) -> BodyMetricsProgress:
        entries = await self.repo.list_in_range(
            user_id, window.date_from, window.date_to
        )
        return BodyMetricsProgress(
            date_from=window.date_from,
            date_to=window.date_to,
            weight_kg=_metric_progress(entries, "weight_kg"),
            body_fat_percent=_metric_progress(entries, "body_fat_percent"),
            muscle_mass_kg=_metric_progress(entries, "muscle_mass_kg"),
            visceral_fat_level=_metric_progress(entries, "visceral_fat_level"),
            measured_bmr_kcal=_metric_progress(entries, "measured_bmr_kcal"),
        )

    async def delete(self, user_id: int, log_id: int) -> None:
        entry = await self.repo.get(user_id, log_id)
        if entry is None:
            raise BodyMetricsError(
                BodyMetricsErrorCode.LOG_NOT_FOUND, "No log entry with that id"
            )
        await self.repo.delete(entry)
