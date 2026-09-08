"""User-owned workout templates: reusable plans, and the two conversions that
make them worth having — session from template, template from session.

Scoped to the caller throughout. A template belonging to someone else is
reported as *not found* rather than *forbidden*, matching
`app/services/training.py`.
"""

import enum
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import Exercise
from app.models.template import TemplateExercise, WorkoutTemplate
from app.models.training import WorkoutSession
from app.repositories.catalog import CatalogRepository
from app.repositories.template import TemplateRepository
from app.schemas.template import (
    TemplateExerciseCreate,
    TemplateExerciseUpdate,
    TemplateFromSession,
    TemplateInstantiate,
    WorkoutTemplateCreate,
    WorkoutTemplateUpdate,
)
from app.schemas.training import SessionExerciseCreate, WorkoutSessionCreate
from app.services.google_calendar import GoogleCalendarService
from app.services.training import TrainingError, TrainingService


class TemplateErrorCode(enum.StrEnum):
    TEMPLATE_NOT_FOUND = "template_not_found"
    TEMPLATE_EXERCISE_NOT_FOUND = "template_exercise_not_found"
    SESSION_NOT_FOUND = "session_not_found"
    UNKNOWN_EXERCISE = "unknown_exercise"
    ORDER_TAKEN = "order_taken"
    NAME_TAKEN = "name_taken"


class TemplateError(Exception):
    def __init__(self, code: TemplateErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _dec(value: float | None) -> Decimal | None:
    """The columns are NUMERIC; going through `str` avoids the binary-float
    artefacts that `Decimal(62.5000000001)` would otherwise store."""
    return None if value is None else Decimal(str(value))


class TemplateService:
    def __init__(
        self,
        session: AsyncSession,
        calendar: GoogleCalendarService | None = None,
    ) -> None:
        self.repo = TemplateRepository(session)
        self.catalog = CatalogRepository(session)
        # Sessions are created through the training service so instantiating a
        # template goes down the same validated path as a hand-built session
        # — Calendar sync included, since a session instantiated as PLANNED
        # is exactly the case that path is meant to sync.
        self.training = TrainingService(session, calendar)

    # --- templates --------------------------------------------------------

    async def list(
        self, user_id: int, search: str | None, *, limit: int, offset: int
    ) -> tuple[list[WorkoutTemplate], int]:
        total = await self.repo.count(user_id, search)
        items = await self.repo.list(user_id, search, limit=limit, offset=offset)
        return items, total

    async def get(self, user_id: int, template_id: int) -> WorkoutTemplate:
        template = await self.repo.get(user_id, template_id)
        if template is None:
            raise TemplateError(
                TemplateErrorCode.TEMPLATE_NOT_FOUND,
                f"No template with id {template_id}",
            )
        return template

    async def create(
        self, user_id: int, payload: WorkoutTemplateCreate
    ) -> WorkoutTemplate:
        await self._require_free_name(user_id, payload.name)
        catalog = await self._catalog_for(e.exercise_id for e in payload.exercises)

        template = WorkoutTemplate(
            user_id=user_id,
            name=payload.name,
            program_day=payload.program_day,
            notes=payload.notes,
            # Explicitly empty: after the flush an untouched collection counts
            # as unloaded, and serialising it would emit a lazy load.
            exercises=[],
        )

        seen: set[int] = set()
        for position, item in enumerate(payload.exercises, start=1):
            order_index = item.order_index or position
            if order_index in seen:
                raise TemplateError(
                    TemplateErrorCode.ORDER_TAKEN,
                    f"Duplicate order_index {order_index} in the payload",
                )
            seen.add(order_index)
            template.exercises.append(
                _new_slot(item, catalog[item.exercise_id], order_index)
            )

        self.repo.add(template)
        await self.repo.flush()
        return template

    async def update(
        self, user_id: int, template_id: int, payload: WorkoutTemplateUpdate
    ) -> WorkoutTemplate:
        template = await self.get(user_id, template_id)
        fields = payload.model_dump(exclude_unset=True)
        if "name" in fields and fields["name"] != template.name:
            await self._require_free_name(user_id, fields["name"], exclude=template_id)
        for key, value in fields.items():
            setattr(template, key, value)
        await self.repo.flush()
        # `updated_at` is an `onupdate` server call, so the flush leaves it
        # expired; re-reading fills it in instead of lazy-loading mid-response.
        return await self.get(user_id, template_id)

    async def delete(self, user_id: int, template_id: int) -> None:
        template = await self.get(user_id, template_id)
        await self.repo.delete(template)

    # --- slots ------------------------------------------------------------

    async def add_exercise(
        self, user_id: int, template_id: int, payload: TemplateExerciseCreate
    ) -> TemplateExercise:
        template = await self.get(user_id, template_id)
        catalog = await self._catalog_for([payload.exercise_id])

        if payload.order_index is None:
            order_index = await self.repo.next_order_index(template.id)
        else:
            order_index = payload.order_index
            if await self.repo.order_index_taken(template.id, order_index):
                raise TemplateError(
                    TemplateErrorCode.ORDER_TAKEN,
                    f"order_index {order_index} is already used in this template",
                )

        item = _new_slot(payload, catalog[payload.exercise_id], order_index)
        template.exercises.append(item)
        await self.repo.flush()
        return item

    async def get_exercise(
        self, user_id: int, template_id: int, template_exercise_id: int
    ) -> TemplateExercise:
        item = await self.repo.get_exercise(user_id, template_id, template_exercise_id)
        if item is None:
            raise TemplateError(
                TemplateErrorCode.TEMPLATE_EXERCISE_NOT_FOUND,
                f"No exercise with id {template_exercise_id} in this template",
            )
        return item

    async def update_exercise(
        self,
        user_id: int,
        template_id: int,
        template_exercise_id: int,
        payload: TemplateExerciseUpdate,
    ) -> TemplateExercise:
        item = await self.get_exercise(user_id, template_id, template_exercise_id)
        fields = payload.model_dump(exclude_unset=True)

        order_index = fields.get("order_index")
        if (
            order_index is not None
            and order_index != item.order_index
            and await self.repo.order_index_taken(
                item.template_id, order_index, exclude_id=item.id
            )
        ):
            raise TemplateError(
                TemplateErrorCode.ORDER_TAKEN,
                f"order_index {order_index} is already used in this template",
            )
        if "target_weight_kg" in fields:
            fields["target_weight_kg"] = _dec(fields["target_weight_kg"])

        for key, value in fields.items():
            setattr(item, key, value)
        await self.repo.flush()
        return item

    async def delete_exercise(
        self, user_id: int, template_id: int, template_exercise_id: int
    ) -> None:
        item = await self.get_exercise(user_id, template_id, template_exercise_id)
        await self.repo.delete_exercise(item)

    # --- conversions ------------------------------------------------------

    async def instantiate(
        self, user_id: int, template_id: int, payload: TemplateInstantiate
    ) -> WorkoutSession:
        """A copy, not a link. The session keeps working — and keeps its
        targets — after the template is edited or deleted."""
        template = await self.get(user_id, template_id)
        create = WorkoutSessionCreate(
            session_date=payload.session_date,
            bodyweight_kg=payload.bodyweight_kg,
            program_day=template.program_day or template.name,
            notes=payload.notes,
            exercises=[
                SessionExerciseCreate(
                    exercise_id=slot.exercise_id,
                    order_index=slot.order_index,
                    target_sets=slot.target_sets,
                    target_reps_min=slot.target_reps_min,
                    target_reps_max=slot.target_reps_max,
                    target_weight_kg=_float(slot.target_weight_kg),
                    notes=slot.notes,
                )
                for slot in template.exercises
            ],
        )
        try:
            return await self.training.create_session(user_id, create)
        except TrainingError as exc:
            raise TemplateError(
                TemplateErrorCode.UNKNOWN_EXERCISE, exc.message
            ) from exc

    async def from_session(
        self, user_id: int, session_id: int, payload: TemplateFromSession
    ) -> WorkoutTemplate:
        """Targets come from the session's *plan*, not from what was lifted: a
        template is what to aim for, and copying the last set's weight would
        bake a bad day into the plan."""
        try:
            workout = await self.training.get_session(user_id, session_id)
        except TrainingError as exc:
            raise TemplateError(
                TemplateErrorCode.SESSION_NOT_FOUND, exc.message
            ) from exc

        return await self.create(
            user_id,
            WorkoutTemplateCreate(
                name=payload.name,
                program_day=workout.program_day,
                notes=payload.notes,
                exercises=[
                    TemplateExerciseCreate(
                        exercise_id=item.exercise_id,
                        order_index=item.order_index,
                        target_sets=item.target_sets,
                        target_reps_min=item.target_reps_min,
                        target_reps_max=item.target_reps_max,
                        target_weight_kg=_float(item.target_weight_kg),
                        notes=item.notes,
                    )
                    for item in workout.exercises
                ],
            ),
        )

    # --- helpers ----------------------------------------------------------

    async def _require_free_name(
        self, user_id: int, name: str, *, exclude: int | None = None
    ) -> None:
        if await self.repo.name_taken(user_id, name, exclude_id=exclude):
            raise TemplateError(
                TemplateErrorCode.NAME_TAKEN, f"You already have a template '{name}'"
            )

    async def _catalog_for(self, exercise_ids: Iterable[int]) -> dict[int, Exercise]:
        """Resolves every referenced exercise up front and reports *all* unknown
        ids at once, the way scripts/seed.py validates."""
        wanted = list(exercise_ids)
        found = await self.catalog.exercises_by_id(wanted)
        missing = sorted({i for i in wanted if i not in found})
        if missing:
            raise TemplateError(
                TemplateErrorCode.UNKNOWN_EXERCISE,
                "Unknown exercise_id: " + ", ".join(str(i) for i in missing),
            )
        return found


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _new_slot(
    payload: TemplateExerciseCreate, exercise: Exercise, order_index: int
) -> TemplateExercise:
    return TemplateExercise(
        # The ORM object rather than the bare id, so `.exercise` stays loaded.
        exercise=exercise,
        order_index=order_index,
        target_sets=payload.target_sets,
        target_reps_min=payload.target_reps_min,
        target_reps_max=payload.target_reps_max,
        target_weight_kg=_dec(payload.target_weight_kg),
        notes=payload.notes,
    )
