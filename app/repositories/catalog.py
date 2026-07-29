"""Data access for catalog master data (muscle groups, exercises)."""

from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.catalog import (
    EquipmentType,
    Exercise,
    ExerciseMuscle,
    ForceType,
    MovementPattern,
    MuscleGroup,
    MuscleRole,
)


@dataclass(frozen=True, slots=True)
class ExerciseFilters:
    search: str | None = None
    pattern: MovementPattern | None = None
    equipment: EquipmentType | None = None
    force: ForceType | None = None
    muscle: str | None = None
    role: MuscleRole | None = None
    is_unilateral: bool | None = None
    requires_overhead: bool | None = None


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_muscle_groups(
        self, *, trackable_only: bool = False
    ) -> list[MuscleGroup]:
        stmt = select(MuscleGroup).order_by(MuscleGroup.depth, MuscleGroup.code)
        if trackable_only:
            stmt = stmt.where(MuscleGroup.is_trackable.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def exercises_by_id(
        self, exercise_ids: Collection[int]
    ) -> dict[int, Exercise]:
        """The ORM objects, not just an existence flag: assigning a loaded
        `Exercise` to a new session/template slot keeps the relationship
        populated, so rendering a freshly created row never triggers a lazy
        load — which would raise under asyncio."""
        if not exercise_ids:
            return {}
        result = await self.session.execute(
            select(Exercise).where(Exercise.id.in_(set(exercise_ids)))
        )
        return {e.id: e for e in result.unique().scalars()}

    async def get_exercise_by_slug(self, slug: str) -> Exercise | None:
        result = await self.session.execute(
            select(Exercise)
            .where(Exercise.slug == slug)
            .options(selectinload(Exercise.muscles))
        )
        return result.unique().scalar_one_or_none()

    async def count_exercises(self, filters: ExerciseFilters) -> int:
        stmt = _apply(select(func.count(Exercise.id)), filters)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def list_exercises(
        self, filters: ExerciseFilters, *, limit: int, offset: int
    ) -> list[Exercise]:
        stmt = (
            _apply(select(Exercise), filters)
            .order_by(Exercise.name_en)
            .limit(limit)
            .offset(offset)
            .options(selectinload(Exercise.muscles))
        )
        result = await self.session.execute(stmt)
        return list(result.unique().scalars())


def _apply(stmt: Select, f: ExerciseFilters) -> Select:
    """Shared WHERE clause, so the count and the page always agree."""
    if f.search:
        term = f"%{f.search}%"
        stmt = stmt.where(
            Exercise.name_en.ilike(term)
            | Exercise.name_vi.ilike(term)
            | Exercise.slug.ilike(term)
        )
    if f.pattern is not None:
        stmt = stmt.where(Exercise.pattern == f.pattern)
    if f.equipment is not None:
        stmt = stmt.where(Exercise.equipment == f.equipment)
    if f.force is not None:
        stmt = stmt.where(Exercise.force == f.force)
    if f.is_unilateral is not None:
        stmt = stmt.where(Exercise.is_unilateral.is_(f.is_unilateral))
    if f.requires_overhead is not None:
        stmt = stmt.where(Exercise.requires_overhead.is_(f.requires_overhead))
    if f.muscle is not None or f.role is not None:
        # EXISTS rather than a join: an exercise maps to several muscles and a
        # join would multiply the rows, breaking both COUNT and LIMIT.
        link = select(ExerciseMuscle.exercise_id).where(
            ExerciseMuscle.exercise_id == Exercise.id
        )
        if f.muscle is not None:
            link = link.join(
                MuscleGroup, MuscleGroup.id == ExerciseMuscle.muscle_group_id
            ).where(MuscleGroup.code == f.muscle)
        if f.role is not None:
            link = link.where(ExerciseMuscle.role == f.role)
        stmt = stmt.where(link.exists())
    return stmt
