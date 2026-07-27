"""Data access for workout logging. Every lookup is scoped by `user_id` — a
session belongs to exactly one user and there is no shared-session concept, so
scoping here means no caller can forget it."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import Exercise
from app.models.training import (
    SessionExercise,
    SessionStatus,
    SetLog,
    WorkoutSession,
)


@dataclass(frozen=True, slots=True)
class SessionFilters:
    status: SessionStatus | None = None
    date_from: date | None = None
    date_to: date | None = None
    program_day: str | None = None


class TrainingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- sessions ---------------------------------------------------------

    async def count_sessions(self, user_id: int, filters: SessionFilters) -> int:
        stmt = _apply(select(func.count(WorkoutSession.id)), user_id, filters)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def list_sessions(
        self, user_id: int, filters: SessionFilters, *, limit: int, offset: int
    ) -> list[WorkoutSession]:
        stmt = (
            _apply(select(WorkoutSession), user_id, filters)
            .order_by(WorkoutSession.session_date.desc(), WorkoutSession.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.unique().scalars())

    async def get_session(self, user_id: int, session_id: int) -> WorkoutSession | None:
        result = await self.session.execute(
            select(WorkoutSession)
            .where(WorkoutSession.id == session_id, WorkoutSession.user_id == user_id)
            # Without this, a re-read returns the identity-mapped object with the
            # collections it already had — a set deleted through its own endpoint
            # would still be listed under the session.
            .execution_options(populate_existing=True)
        )
        return result.unique().scalar_one_or_none()

    def add_session(self, workout: WorkoutSession) -> None:
        self.session.add(workout)

    async def delete_session(self, workout: WorkoutSession) -> None:
        await self.session.delete(workout)
        await self.session.flush()

    # --- session exercises ------------------------------------------------

    async def get_session_exercise(
        self, user_id: int, session_id: int, session_exercise_id: int
    ) -> SessionExercise | None:
        """Joined against the parent session so a foreign id cannot be reached
        by guessing it — ownership is part of the query, not a later check."""
        result = await self.session.execute(
            select(SessionExercise)
            .join(WorkoutSession, WorkoutSession.id == SessionExercise.session_id)
            .where(
                SessionExercise.id == session_exercise_id,
                SessionExercise.session_id == session_id,
                WorkoutSession.user_id == user_id,
            )
        )
        return result.unique().scalar_one_or_none()

    async def next_order_index(self, session_id: int) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.max(SessionExercise.order_index), 0)).where(
                SessionExercise.session_id == session_id
            )
        )
        return result.scalar_one() + 1

    async def order_index_taken(
        self, session_id: int, order_index: int, *, exclude_id: int | None = None
    ) -> bool:
        stmt = select(SessionExercise.id).where(
            SessionExercise.session_id == session_id,
            SessionExercise.order_index == order_index,
        )
        if exclude_id is not None:
            stmt = stmt.where(SessionExercise.id != exclude_id)
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def delete_session_exercise(self, item: SessionExercise) -> None:
        await self.session.delete(item)
        await self.session.flush()

    # --- set logs ---------------------------------------------------------

    async def get_set(
        self, user_id: int, session_exercise_id: int, set_id: int
    ) -> SetLog | None:
        result = await self.session.execute(
            select(SetLog)
            .join(SessionExercise, SessionExercise.id == SetLog.session_exercise_id)
            .join(WorkoutSession, WorkoutSession.id == SessionExercise.session_id)
            .where(
                SetLog.id == set_id,
                SetLog.session_exercise_id == session_exercise_id,
                WorkoutSession.user_id == user_id,
            )
        )
        return result.unique().scalar_one_or_none()

    async def next_set_index(self, session_exercise_id: int) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.max(SetLog.set_index), 0)).where(
                SetLog.session_exercise_id == session_exercise_id
            )
        )
        return result.scalar_one() + 1

    async def set_index_taken(self, session_exercise_id: int, set_index: int) -> bool:
        result = await self.session.execute(
            select(SetLog.id)
            .where(
                SetLog.session_exercise_id == session_exercise_id,
                SetLog.set_index == set_index,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def delete_set(self, item: SetLog) -> None:
        await self.session.delete(item)
        await self.session.flush()

    # --- catalog cross-check ----------------------------------------------

    async def exercises_by_id(
        self, exercise_ids: Collection[int]
    ) -> dict[int, Exercise]:
        """Returns the ORM objects, not just a existence flag: assigning
        `SessionExercise.exercise` keeps the relationship loaded, so rendering a
        freshly created row never triggers a lazy load (which would raise under
        asyncio)."""
        if not exercise_ids:
            return {}
        result = await self.session.execute(
            select(Exercise).where(Exercise.id.in_(set(exercise_ids)))
        )
        return {e.id: e for e in result.unique().scalars()}

    async def flush(self) -> None:
        await self.session.flush()


def _apply(stmt: Select, user_id: int, f: SessionFilters) -> Select:
    """Shared WHERE clause, so the count and the page always agree."""
    stmt = stmt.where(WorkoutSession.user_id == user_id)
    if f.status is not None:
        stmt = stmt.where(WorkoutSession.status == f.status)
    if f.date_from is not None:
        stmt = stmt.where(WorkoutSession.session_date >= f.date_from)
    if f.date_to is not None:
        stmt = stmt.where(WorkoutSession.session_date <= f.date_to)
    if f.program_day is not None:
        stmt = stmt.where(WorkoutSession.program_day == f.program_day)
    return stmt
