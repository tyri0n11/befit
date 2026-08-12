"""Data access for user-owned workout templates. Scoped by `user_id` for the
same reason as `app/repositories/training.py`: ownership belongs in the query,
not in a check a caller can forget."""

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import TemplateExercise, WorkoutTemplate


class TemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count(self, user_id: int, search: str | None) -> int:
        stmt = _apply(select(func.count(WorkoutTemplate.id)), user_id, search)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def list(
        self, user_id: int, search: str | None, *, limit: int, offset: int
    ) -> list[WorkoutTemplate]:
        stmt = (
            _apply(select(WorkoutTemplate), user_id, search)
            .order_by(WorkoutTemplate.name)
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.unique().scalars())

    async def get(self, user_id: int, template_id: int) -> WorkoutTemplate | None:
        result = await self.session.execute(
            select(WorkoutTemplate)
            .where(
                WorkoutTemplate.id == template_id,
                WorkoutTemplate.user_id == user_id,
            )
            # Same reason as TrainingRepository.get_session: without it a re-read
            # returns the identity-mapped object with the collection it already
            # had, so a slot deleted through its own endpoint would still show.
            .execution_options(populate_existing=True)
        )
        return result.unique().scalar_one_or_none()

    async def name_taken(
        self, user_id: int, name: str, *, exclude_id: int | None = None
    ) -> bool:
        """Checked up front so a clash is a 409 rather than an IntegrityError
        from `uq_template_name`."""
        stmt = select(WorkoutTemplate.id).where(
            WorkoutTemplate.user_id == user_id, WorkoutTemplate.name == name
        )
        if exclude_id is not None:
            stmt = stmt.where(WorkoutTemplate.id != exclude_id)
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def get_exercise(
        self, user_id: int, template_id: int, template_exercise_id: int
    ) -> TemplateExercise | None:
        result = await self.session.execute(
            select(TemplateExercise)
            .join(WorkoutTemplate, WorkoutTemplate.id == TemplateExercise.template_id)
            .where(
                TemplateExercise.id == template_exercise_id,
                TemplateExercise.template_id == template_id,
                WorkoutTemplate.user_id == user_id,
            )
        )
        return result.unique().scalar_one_or_none()

    async def next_order_index(self, template_id: int) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.max(TemplateExercise.order_index), 0)).where(
                TemplateExercise.template_id == template_id
            )
        )
        return result.scalar_one() + 1

    async def order_index_taken(
        self, template_id: int, order_index: int, *, exclude_id: int | None = None
    ) -> bool:
        stmt = select(TemplateExercise.id).where(
            TemplateExercise.template_id == template_id,
            TemplateExercise.order_index == order_index,
        )
        if exclude_id is not None:
            stmt = stmt.where(TemplateExercise.id != exclude_id)
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    def add(self, template: WorkoutTemplate) -> None:
        self.session.add(template)

    async def delete(self, template: WorkoutTemplate) -> None:
        await self.session.delete(template)
        await self.session.flush()

    async def delete_exercise(self, item: TemplateExercise) -> None:
        await self.session.delete(item)
        await self.session.flush()

    async def flush(self) -> None:
        await self.session.flush()


def _apply(stmt: Select, user_id: int, search: str | None) -> Select:
    """Shared WHERE clause, so the count and the page always agree."""
    stmt = stmt.where(WorkoutTemplate.user_id == user_id)
    if search:
        stmt = stmt.where(WorkoutTemplate.name.ilike(f"%{search}%"))
    return stmt
