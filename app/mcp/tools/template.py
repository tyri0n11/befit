"""Template tools — auth required, mirrors app/api/v1/endpoints/template.py."""

from app.mcp.context import authenticated_session
from app.mcp.errors import raise_tool_error
from app.mcp.server import mcp
from app.schemas.template import (
    TemplateExerciseCreate,
    TemplateExerciseResponse,
    TemplateExerciseUpdate,
    TemplateFromSession,
    TemplateInstantiate,
    WorkoutTemplateCreate,
    WorkoutTemplatePage,
    WorkoutTemplateResponse,
    WorkoutTemplateSummary,
    WorkoutTemplateUpdate,
)
from app.schemas.training import WorkoutSessionResponse
from app.services.template import TemplateError, TemplateService
from mcp.server.mcpserver import Context


@mcp.tool()
async def list_templates(
    ctx: Context, q: str | None = None, limit: int = 50, offset: int = 0
) -> WorkoutTemplatePage:
    """List the caller's templates, optionally matching `q` against the name."""
    async with authenticated_session(ctx) as (session, user):
        service = TemplateService(session)
        items, total = await service.list(user.id, q, limit=limit, offset=offset)
        return WorkoutTemplatePage(
            items=[WorkoutTemplateSummary.model_validate(t) for t in items],
            total=total,
            limit=limit,
            offset=offset,
        )


@mcp.tool()
async def create_template(
    payload: WorkoutTemplateCreate, ctx: Context
) -> WorkoutTemplateResponse:
    """Create a template, optionally with its exercises."""
    async with authenticated_session(ctx) as (session, user):
        service = TemplateService(session)
        try:
            template = await service.create(user.id, payload)
        except TemplateError as exc:
            raise_tool_error(exc)
        return WorkoutTemplateResponse.model_validate(template)


@mcp.tool()
async def template_from_session(
    session_id: int, payload: TemplateFromSession, ctx: Context
) -> WorkoutTemplateResponse:
    """Save a session's plan as a reusable template. Copies `target_*` values,
    not what was actually lifted."""
    async with authenticated_session(ctx) as (session, user):
        service = TemplateService(session)
        try:
            template = await service.from_session(user.id, session_id, payload)
        except TemplateError as exc:
            raise_tool_error(exc)
        return WorkoutTemplateResponse.model_validate(template)


@mcp.tool()
async def get_template(template_id: int, ctx: Context) -> WorkoutTemplateResponse:
    """One template with its exercises."""
    async with authenticated_session(ctx) as (session, user):
        service = TemplateService(session)
        try:
            template = await service.get(user.id, template_id)
        except TemplateError as exc:
            raise_tool_error(exc)
        return WorkoutTemplateResponse.model_validate(template)


@mcp.tool()
async def update_template(
    template_id: int, payload: WorkoutTemplateUpdate, ctx: Context
) -> WorkoutTemplateResponse:
    """Rename a template or change its notes."""
    async with authenticated_session(ctx) as (session, user):
        service = TemplateService(session)
        try:
            template = await service.update(user.id, template_id, payload)
        except TemplateError as exc:
            raise_tool_error(exc)
        return WorkoutTemplateResponse.model_validate(template)


@mcp.tool()
async def delete_template(template_id: int, ctx: Context) -> None:
    """Delete a template; sessions made from it are untouched."""
    async with authenticated_session(ctx) as (session, user):
        service = TemplateService(session)
        try:
            await service.delete(user.id, template_id)
        except TemplateError as exc:
            raise_tool_error(exc)


@mcp.tool()
async def instantiate_template(
    template_id: int, payload: TemplateInstantiate, ctx: Context
) -> WorkoutSessionResponse:
    """Start a session from this template. Copies, does not link — editing or
    deleting the template afterwards never reaches back into the new session."""
    async with authenticated_session(ctx) as (session, user):
        service = TemplateService(session)
        try:
            workout = await service.instantiate(user.id, template_id, payload)
        except TemplateError as exc:
            raise_tool_error(exc)
        return WorkoutSessionResponse.model_validate(workout)


@mcp.tool()
async def add_template_exercise(
    template_id: int, payload: TemplateExerciseCreate, ctx: Context
) -> TemplateExerciseResponse:
    """Add an exercise to a template."""
    async with authenticated_session(ctx) as (session, user):
        service = TemplateService(session)
        try:
            item = await service.add_exercise(user.id, template_id, payload)
        except TemplateError as exc:
            raise_tool_error(exc)
        return TemplateExerciseResponse.model_validate(item)


@mcp.tool()
async def update_template_exercise(
    template_id: int,
    template_exercise_id: int,
    payload: TemplateExerciseUpdate,
    ctx: Context,
) -> TemplateExerciseResponse:
    """Update one exercise's targets or order in a template."""
    async with authenticated_session(ctx) as (session, user):
        service = TemplateService(session)
        try:
            item = await service.update_exercise(
                user.id, template_id, template_exercise_id, payload
            )
        except TemplateError as exc:
            raise_tool_error(exc)
        return TemplateExerciseResponse.model_validate(item)


@mcp.tool()
async def delete_template_exercise(
    template_id: int, template_exercise_id: int, ctx: Context
) -> None:
    """Remove an exercise from a template."""
    async with authenticated_session(ctx) as (session, user):
        service = TemplateService(session)
        try:
            await service.delete_exercise(user.id, template_id, template_exercise_id)
        except TemplateError as exc:
            raise_tool_error(exc)
