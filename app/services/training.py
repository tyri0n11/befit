"""Workout logging: sessions, the exercises inside them, and the sets logged
against each one.

Everything is user-scoped. A session that belongs to someone else is reported
as *not found* rather than *forbidden*, so ids are not probeable.
"""

import enum
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import Exercise
from app.models.training import SessionExercise, SetLog, WorkoutSession
from app.repositories.training import SessionFilters, TrainingRepository
from app.schemas.training import (
    SessionExerciseCreate,
    SessionExerciseUpdate,
    SetLogCreate,
    SetLogUpdate,
    WorkoutSessionCreate,
    WorkoutSessionPage,
    WorkoutSessionSummary,
    WorkoutSessionUpdate,
)


class TrainingErrorCode(enum.StrEnum):
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_EXERCISE_NOT_FOUND = "session_exercise_not_found"
    SET_NOT_FOUND = "set_not_found"
    UNKNOWN_EXERCISE = "unknown_exercise"
    ORDER_TAKEN = "order_taken"
    SET_INDEX_TAKEN = "set_index_taken"


class TrainingError(Exception):
    def __init__(self, code: TrainingErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _dec(value: float | None) -> Decimal | None:
    """The columns are NUMERIC; going through `str` avoids the binary-float
    artefacts that `Decimal(62.5000000001)` would otherwise store."""
    return None if value is None else Decimal(str(value))


class TrainingService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = TrainingRepository(session)

    # --- sessions ---------------------------------------------------------

    async def list_sessions(
        self, user_id: int, filters: SessionFilters, *, limit: int, offset: int
    ) -> WorkoutSessionPage:
        total = await self.repo.count_sessions(user_id, filters)
        items = await self.repo.list_sessions(
            user_id, filters, limit=limit, offset=offset
        )
        return WorkoutSessionPage(
            items=[WorkoutSessionSummary.model_validate(s) for s in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_session(self, user_id: int, session_id: int) -> WorkoutSession:
        workout = await self.repo.get_session(user_id, session_id)
        if workout is None:
            raise TrainingError(
                TrainingErrorCode.SESSION_NOT_FOUND,
                f"No workout session with id {session_id}",
            )
        return workout

    async def create_session(
        self, user_id: int, payload: WorkoutSessionCreate
    ) -> WorkoutSession:
        catalog = await self._catalog_for(e.exercise_id for e in payload.exercises)

        workout = WorkoutSession(
            user_id=user_id,
            session_date=payload.session_date,
            status=payload.status,
            bodyweight_kg=_dec(payload.bodyweight_kg),
            program_day=payload.program_day,
            notes=payload.notes,
            # Explicitly empty, not left unset: once the flush makes the row
            # persistent an untouched collection is *unloaded*, and reading it
            # to build the response would emit a lazy load under asyncio.
            exercises=[],
        )

        seen: set[int] = set()
        for position, item in enumerate(payload.exercises, start=1):
            order_index = item.order_index or position
            if order_index in seen:
                raise TrainingError(
                    TrainingErrorCode.ORDER_TAKEN,
                    f"Duplicate order_index {order_index} in the payload",
                )
            seen.add(order_index)
            workout.exercises.append(
                _new_session_exercise(item, catalog[item.exercise_id], order_index)
            )

        self.repo.add_session(workout)
        await self.repo.flush()
        return workout

    async def update_session(
        self, user_id: int, session_id: int, payload: WorkoutSessionUpdate
    ) -> WorkoutSession:
        workout = await self.get_session(user_id, session_id)
        fields = payload.model_dump(exclude_unset=True)
        if "bodyweight_kg" in fields:
            fields["bodyweight_kg"] = _dec(fields["bodyweight_kg"])
        for key, value in fields.items():
            setattr(workout, key, value)
        await self.repo.flush()
        # `updated_at` is an `onupdate` server call, so the flush leaves it
        # expired; re-reading fills it in instead of lazy-loading mid-response.
        return await self.get_session(user_id, session_id)

    async def delete_session(self, user_id: int, session_id: int) -> None:
        workout = await self.get_session(user_id, session_id)
        await self.repo.delete_session(workout)

    # --- session exercises ------------------------------------------------

    async def add_exercise(
        self, user_id: int, session_id: int, payload: SessionExerciseCreate
    ) -> SessionExercise:
        workout = await self.get_session(user_id, session_id)
        catalog = await self._catalog_for([payload.exercise_id])

        if payload.order_index is None:
            order_index = await self.repo.next_order_index(workout.id)
        else:
            order_index = payload.order_index
            if await self.repo.order_index_taken(workout.id, order_index):
                raise TrainingError(
                    TrainingErrorCode.ORDER_TAKEN,
                    f"order_index {order_index} is already used in this session",
                )

        item = _new_session_exercise(payload, catalog[payload.exercise_id], order_index)
        workout.exercises.append(item)
        await self.repo.flush()
        return item

    async def get_exercise(
        self, user_id: int, session_id: int, session_exercise_id: int
    ) -> SessionExercise:
        item = await self.repo.get_session_exercise(
            user_id, session_id, session_exercise_id
        )
        if item is None:
            raise TrainingError(
                TrainingErrorCode.SESSION_EXERCISE_NOT_FOUND,
                f"No exercise with id {session_exercise_id} in this session",
            )
        return item

    async def update_exercise(
        self,
        user_id: int,
        session_id: int,
        session_exercise_id: int,
        payload: SessionExerciseUpdate,
    ) -> SessionExercise:
        item = await self.get_exercise(user_id, session_id, session_exercise_id)
        fields = payload.model_dump(exclude_unset=True)

        order_index = fields.get("order_index")
        if (
            order_index is not None
            and order_index != item.order_index
            and await self.repo.order_index_taken(
                item.session_id, order_index, exclude_id=item.id
            )
        ):
            raise TrainingError(
                TrainingErrorCode.ORDER_TAKEN,
                f"order_index {order_index} is already used in this session",
            )
        if "target_weight_kg" in fields:
            fields["target_weight_kg"] = _dec(fields["target_weight_kg"])

        for key, value in fields.items():
            setattr(item, key, value)
        await self.repo.flush()
        return item

    async def delete_exercise(
        self, user_id: int, session_id: int, session_exercise_id: int
    ) -> None:
        item = await self.get_exercise(user_id, session_id, session_exercise_id)
        await self.repo.delete_session_exercise(item)

    # --- set logs ---------------------------------------------------------

    async def log_set(
        self,
        user_id: int,
        session_id: int,
        session_exercise_id: int,
        payload: SetLogCreate,
    ) -> SetLog:
        item = await self.get_exercise(user_id, session_id, session_exercise_id)

        if payload.set_index is None:
            set_index = await self.repo.next_set_index(item.id)
        else:
            set_index = payload.set_index
            if await self.repo.set_index_taken(item.id, set_index):
                raise TrainingError(
                    TrainingErrorCode.SET_INDEX_TAKEN,
                    f"set_index {set_index} is already logged for this exercise",
                )

        log = SetLog(
            set_index=set_index,
            reps=payload.reps,
            weight_kg=_dec(payload.weight_kg),
            rpe=_dec(payload.rpe),
            rest_before_sec=payload.rest_before_sec,
            rom_note=payload.rom_note,
        )
        item.sets.append(log)
        await self.repo.flush()
        return log

    async def update_set(
        self,
        user_id: int,
        session_exercise_id: int,
        set_id: int,
        payload: SetLogUpdate,
    ) -> SetLog:
        log = await self.repo.get_set(user_id, session_exercise_id, set_id)
        if log is None:
            raise TrainingError(
                TrainingErrorCode.SET_NOT_FOUND, f"No set with id {set_id}"
            )
        fields = payload.model_dump(exclude_unset=True)
        for key in ("weight_kg", "rpe"):
            if key in fields:
                fields[key] = _dec(fields[key])
        for key, value in fields.items():
            setattr(log, key, value)
        await self.repo.flush()
        return log

    async def delete_set(
        self, user_id: int, session_exercise_id: int, set_id: int
    ) -> None:
        log = await self.repo.get_set(user_id, session_exercise_id, set_id)
        if log is None:
            raise TrainingError(
                TrainingErrorCode.SET_NOT_FOUND, f"No set with id {set_id}"
            )
        await self.repo.delete_set(log)

    # --- helpers ----------------------------------------------------------

    async def _catalog_for(self, exercise_ids: Iterable[int]) -> dict[int, Exercise]:
        """Resolves every referenced exercise up front and reports *all* unknown
        ids at once, the way scripts/seed.py validates."""
        wanted = list(exercise_ids)
        found = await self.repo.exercises_by_id(wanted)
        missing = sorted({i for i in wanted if i not in found})
        if missing:
            raise TrainingError(
                TrainingErrorCode.UNKNOWN_EXERCISE,
                "Unknown exercise_id: " + ", ".join(str(i) for i in missing),
            )
        return found


def _new_session_exercise(
    payload: SessionExerciseCreate, exercise: Exercise, order_index: int
) -> SessionExercise:
    return SessionExercise(
        # The ORM object rather than the bare id, so `.exercise` stays loaded;
        # `sets` for the same reason as `WorkoutSession.exercises` above.
        exercise=exercise,
        sets=[],
        order_index=order_index,
        target_sets=payload.target_sets,
        target_reps_min=payload.target_reps_min,
        target_reps_max=payload.target_reps_max,
        target_weight_kg=_dec(payload.target_weight_kg),
        notes=payload.notes,
    )
