"""Session tools — auth required, mirrors app/api/v1/endpoints/training.py."""

from datetime import date

from app.mcp.context import authenticated_session
from app.mcp.errors import raise_tool_error
from app.mcp.server import mcp
from app.models.training import SessionStatus
from app.repositories.training import SessionFilters
from app.schemas.training import (
    SessionExerciseCreate,
    SessionExerciseResponse,
    SessionExerciseUpdate,
    SetLogCreate,
    SetLogResponse,
    SetLogUpdate,
    WorkoutSessionCreate,
    WorkoutSessionPage,
    WorkoutSessionResponse,
    WorkoutSessionUpdate,
)
from app.services.training import TrainingError, TrainingService
from mcp.server.mcpserver import Context


@mcp.tool()
async def list_sessions(
    ctx: Context,
    status: SessionStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    program_day: str | None = None,
    include_deleted: bool = False,
    deleted_only: bool = False,
    limit: int = 30,
    offset: int = 0,
) -> WorkoutSessionPage:
    """List the caller's workout sessions."""
    filters = SessionFilters(
        status=status,
        date_from=date_from,
        date_to=date_to,
        program_day=program_day,
        include_deleted=include_deleted,
        deleted_only=deleted_only,
    )
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        return await service.list_sessions(user.id, filters, limit=limit, offset=offset)


@mcp.tool()
async def create_session(
    payload: WorkoutSessionCreate, ctx: Context
) -> WorkoutSessionResponse:
    """Create a session, optionally with its planned exercises."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            workout = await service.create_session(user.id, payload)
        except TrainingError as exc:
            raise_tool_error(exc)
        return WorkoutSessionResponse.model_validate(workout)


@mcp.tool()
async def get_session(session_id: int, ctx: Context) -> WorkoutSessionResponse:
    """One session with its exercises and sets."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            workout = await service.get_session(user.id, session_id)
        except TrainingError as exc:
            raise_tool_error(exc)
        return WorkoutSessionResponse.model_validate(workout)


@mcp.tool()
async def update_session(
    session_id: int, payload: WorkoutSessionUpdate, ctx: Context
) -> WorkoutSessionResponse:
    """Update a session's status, bodyweight or notes."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            workout = await service.update_session(user.id, session_id, payload)
        except TrainingError as exc:
            raise_tool_error(exc)
        return WorkoutSessionResponse.model_validate(workout)


@mcp.tool()
async def delete_session(session_id: int, ctx: Context) -> None:
    """Move a session to the bin (soft delete). Reversible with restore_session."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            await service.delete_session(user.id, session_id)
        except TrainingError as exc:
            raise_tool_error(exc)


@mcp.tool()
async def restore_session(session_id: int, ctx: Context) -> WorkoutSessionResponse:
    """Restore a soft-deleted session."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            workout = await service.restore_session(user.id, session_id)
        except TrainingError as exc:
            raise_tool_error(exc)
        return WorkoutSessionResponse.model_validate(workout)


@mcp.tool()
async def purge_session(session_id: int, ctx: Context) -> None:
    """Permanently delete a session already in the bin. Irreversible."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            await service.purge_session(user.id, session_id)
        except TrainingError as exc:
            raise_tool_error(exc)


@mcp.tool()
async def add_session_exercise(
    session_id: int, payload: SessionExerciseCreate, ctx: Context
) -> SessionExerciseResponse:
    """Add an exercise to a session."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            item = await service.add_exercise(user.id, session_id, payload)
        except TrainingError as exc:
            raise_tool_error(exc)
        return SessionExerciseResponse.model_validate(item)


@mcp.tool()
async def update_session_exercise(
    session_id: int,
    session_exercise_id: int,
    payload: SessionExerciseUpdate,
    ctx: Context,
) -> SessionExerciseResponse:
    """Update targets, order or status of one exercise in a session."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            item = await service.update_exercise(
                user.id, session_id, session_exercise_id, payload
            )
        except TrainingError as exc:
            raise_tool_error(exc)
        return SessionExerciseResponse.model_validate(item)


@mcp.tool()
async def delete_session_exercise(
    session_id: int, session_exercise_id: int, ctx: Context
) -> None:
    """Remove an exercise and its sets from a session."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            await service.delete_exercise(user.id, session_id, session_exercise_id)
        except TrainingError as exc:
            raise_tool_error(exc)


@mcp.tool()
async def log_set(
    session_id: int,
    session_exercise_id: int,
    payload: SetLogCreate,
    ctx: Context,
) -> SetLogResponse:
    """Log a set."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            log = await service.log_set(
                user.id, session_id, session_exercise_id, payload
            )
        except TrainingError as exc:
            raise_tool_error(exc)
        return SetLogResponse.model_validate(log)


@mcp.tool()
async def update_set(
    session_id: int,
    session_exercise_id: int,
    set_id: int,
    payload: SetLogUpdate,
    ctx: Context,
) -> SetLogResponse:
    """Correct a logged set."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            log = await service.update_set(
                user.id, session_exercise_id, set_id, payload
            )
        except TrainingError as exc:
            raise_tool_error(exc)
        return SetLogResponse.model_validate(log)


@mcp.tool()
async def delete_set(
    session_id: int, session_exercise_id: int, set_id: int, ctx: Context
) -> None:
    """Delete a logged set."""
    async with authenticated_session(ctx) as (session, user):
        service = TrainingService(session)
        try:
            await service.delete_set(user.id, session_exercise_id, set_id)
        except TrainingError as exc:
            raise_tool_error(exc)
