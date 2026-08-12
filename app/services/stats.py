"""Volume views over a date range.

The window is either a preset `Period` counted back from today or an explicit
`date_from`/`date_to` pair. Buckets with no training are emitted as zeroes
rather than skipped: a chart must show the week you missed, not close the gap.
"""

from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import MuscleRole
from app.models.training import Bucket
from app.repositories.stats import StatsRange, StatsRepository
from app.schemas.stats import (
    ExerciseVolume,
    ExerciseVolumeReport,
    MuscleVolume,
    MuscleVolumeReport,
    Period,
    VolumePoint,
    VolumeReport,
    VolumeTotals,
)


def resolve_range(
    period: Period | None, date_from: date | None, date_to: date | None
) -> StatsRange:
    """Explicit dates win; `period` only fills in a missing start. Passing both
    a period and a `date_from` is not an error — the caller was specific, and
    the specific value is the one that survives."""
    end = date_to or date.today()
    start = date_from or _start_of(period or Period.WEEK_4, end)
    if start > end:
        raise ValueError("date_from must not be after date_to")
    return StatsRange(date_from=start, date_to=end)


def _start_of(period: Period, end: date) -> date:
    count, unit = int(period.value[:-1]), period.value[-1]
    if unit == "w":
        return end - timedelta(weeks=count)
    return _minus_months(end, count * (12 if unit == "y" else 1))


def _minus_months(day: date, months: int) -> date:
    total = day.year * 12 + (day.month - 1) - months
    year, month = divmod(total, 12)
    # 31 May minus three months has no 31 February; clamp to the month's end.
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


class StatsService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = StatsRepository(session)

    async def volume(
        self, user_id: int, window: StatsRange, bucket: Bucket
    ) -> VolumeReport:
        totals = await self.repo.totals(user_id, window)
        rows = await self.repo.series(user_id, window, bucket)
        logged = {row.bucket: row for row in rows}

        points: list[VolumePoint] = []
        for start in _buckets(window, bucket):
            row = logged.get(start)
            if row is None:
                points.append(
                    VolumePoint(bucket=start, tonnage=0.0, sets=0, reps=0, sessions=0)
                )
            else:
                points.append(
                    VolumePoint(
                        bucket=start,
                        tonnage=float(row.tonnage),
                        sets=row.sets,
                        reps=row.reps,
                        sessions=row.sessions,
                    )
                )
        return VolumeReport(
            date_from=window.date_from,
            date_to=window.date_to,
            bucket=bucket,
            totals=VolumeTotals(
                tonnage=float(totals.tonnage),
                sets=totals.sets,
                reps=totals.reps,
                sessions=totals.sessions,
            ),
            points=points,
        )

    async def by_muscle(
        self, user_id: int, window: StatsRange, role: MuscleRole | None
    ) -> MuscleVolumeReport:
        rows = await self.repo.by_muscle(user_id, window, role)
        return MuscleVolumeReport(
            date_from=window.date_from,
            date_to=window.date_to,
            role=role,
            items=[
                MuscleVolume(
                    code=row.code,
                    name_en=row.name_en,
                    name_vi=row.name_vi,
                    tonnage=float(row.tonnage),
                    sets=row.sets,
                    reps=row.reps,
                )
                for row in rows
            ],
        )

    async def by_exercise(
        self, user_id: int, window: StatsRange, *, limit: int
    ) -> ExerciseVolumeReport:
        rows = await self.repo.by_exercise(user_id, window, limit=limit)
        return ExerciseVolumeReport(
            date_from=window.date_from,
            date_to=window.date_to,
            items=[
                ExerciseVolume(
                    exercise_id=row.exercise_id,
                    slug=row.slug,
                    name_en=row.name_en,
                    tonnage=float(row.tonnage),
                    sets=row.sets,
                    reps=row.reps,
                )
                for row in rows
            ],
        )


def _buckets(window: StatsRange, bucket: Bucket) -> list[date]:
    """Every bucket start in the window, matching what `date_trunc` produces —
    Monday for weeks, the 1st for months. The first and last bucket may extend
    past the window; that is what makes them line up with the aggregate."""
    starts: list[date] = []
    current = _bucket_start(window.date_from, bucket)
    while current <= window.date_to:
        starts.append(current)
        current = _next_bucket(current, bucket)
    return starts


def _bucket_start(day: date, bucket: Bucket) -> date:
    if bucket is Bucket.DAY:
        return day
    if bucket is Bucket.WEEK:
        # date_trunc('week', ...) is ISO — weeks start on Monday.
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def _next_bucket(day: date, bucket: Bucket) -> date:
    if bucket is Bucket.DAY:
        return day + timedelta(days=1)
    if bucket is Bucket.WEEK:
        return day + timedelta(days=7)
    return _minus_months(day, -1)
