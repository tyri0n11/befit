"""Aggregate queries over logged sets.

Every number here is computed by Postgres, never in Python: a year of training
is tens of thousands of `set_logs` rows and summing them in the service would
mean loading all of them.

Tonnage follows the rule stated in the header of `03_init_training.sql` —
`weight_kg * (2 if is_unilateral else 1) * reps`. A bodyweight movement has a
NULL weight and therefore contributes 0 tonnage; it is `reps` that records it,
which is why every query returns both.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Date, Row, Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.catalog import Exercise, ExerciseMuscle, MuscleGroup, MuscleRole
from app.models.training import Bucket, SessionExercise, SetLog, WorkoutSession

# A unilateral movement is logged per side, so both sides count.
_SIDES = case((Exercise.is_unilateral.is_(True), 2), else_=1)

_TONNAGE = func.coalesce(
    func.sum(func.coalesce(SetLog.weight_kg, 0) * _SIDES * SetLog.reps), 0
)
_SETS = func.count(SetLog.id)
_REPS = func.coalesce(func.sum(SetLog.reps), 0)
_SESSIONS = func.count(func.distinct(WorkoutSession.id))


@dataclass(frozen=True, slots=True)
class StatsRange:
    date_from: date
    date_to: date


class StatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def totals(self, user_id: int, window: StatsRange) -> Row:
        stmt = _base(
            select(
                _TONNAGE.label("tonnage"),
                _SETS.label("sets"),
                _REPS.label("reps"),
                _SESSIONS.label("sessions"),
            ),
            user_id,
            window,
        )
        result = await self.session.execute(stmt)
        return result.one()

    async def series(
        self, user_id: int, window: StatsRange, bucket: Bucket
    ) -> list[Row]:
        column = _bucket_column(bucket).label("bucket")
        stmt = (
            _base(
                select(
                    column,
                    _TONNAGE.label("tonnage"),
                    _SETS.label("sets"),
                    _REPS.label("reps"),
                    _SESSIONS.label("sessions"),
                ),
                user_id,
                window,
            )
            .group_by(column)
            .order_by(column)
        )
        result = await self.session.execute(stmt)
        return list(result.all())

    async def by_muscle(
        self, user_id: int, window: StatsRange, role: MuscleRole | None
    ) -> list[Row]:
        """Volume attributed to each muscle group.

        These totals deliberately **do not** sum to the range total: a bench
        press counts once for chest and again for triceps. That is the point of
        the view — it answers "how much did this muscle get", not "how was the
        session split".
        """
        stmt = _base(
            select(
                MuscleGroup.code,
                MuscleGroup.name_en,
                MuscleGroup.name_vi,
                _TONNAGE.label("tonnage"),
                _SETS.label("sets"),
                _REPS.label("reps"),
            ),
            user_id,
            window,
        ).join(ExerciseMuscle, ExerciseMuscle.exercise_id == Exercise.id)
        if role is not None:
            stmt = stmt.where(ExerciseMuscle.role == role)
        stmt = (
            stmt.join(MuscleGroup, MuscleGroup.id == ExerciseMuscle.muscle_group_id)
            .group_by(MuscleGroup.code, MuscleGroup.name_en, MuscleGroup.name_vi)
            .order_by(_SETS.desc(), MuscleGroup.code)
        )
        result = await self.session.execute(stmt)
        return list(result.all())

    async def by_exercise(
        self, user_id: int, window: StatsRange, *, limit: int
    ) -> list[Row]:
        stmt = (
            _base(
                select(
                    Exercise.id.label("exercise_id"),
                    Exercise.slug,
                    Exercise.name_en,
                    _TONNAGE.label("tonnage"),
                    _SETS.label("sets"),
                    _REPS.label("reps"),
                ),
                user_id,
                window,
            )
            .group_by(Exercise.id, Exercise.slug, Exercise.name_en)
            # Tonnage first, then sets so bodyweight work still ranks.
            .order_by(_TONNAGE.desc(), _SETS.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.all())


def _base(stmt: Select, user_id: int, window: StatsRange) -> Select:
    """Driven from `set_logs`: a row exists only for a set that was actually
    performed, so planned-but-unlogged work never inflates the numbers."""
    return (
        stmt.select_from(SetLog)
        .join(SessionExercise, SessionExercise.id == SetLog.session_exercise_id)
        .join(WorkoutSession, WorkoutSession.id == SessionExercise.session_id)
        .join(Exercise, Exercise.id == SessionExercise.exercise_id)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.session_date >= window.date_from,
            WorkoutSession.session_date <= window.date_to,
            # A soft-deleted session must stop counting immediately, otherwise
            # deleting a bad day leaves its tonnage in every chart.
            WorkoutSession.deleted_at.is_(None),
        )
    )


def _bucket_column(bucket: Bucket) -> ColumnElement[date]:
    if bucket is Bucket.DAY:
        return WorkoutSession.session_date
    # date_trunc yields a timestamp; the cast keeps the response a plain date.
    return func.date_trunc(bucket.value, WorkoutSession.session_date).cast(Date)
