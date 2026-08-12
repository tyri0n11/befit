"""Stats tools — auth required, mirrors app/api/v1/endpoints/stats.py."""

from datetime import date

from app.mcp.context import authenticated_session
from app.mcp.server import mcp
from app.models.training import Bucket
from app.repositories.stats import StatsRange
from app.schemas.stats import (
    ExerciseVolumeReport,
    MuscleRoleFilter,
    MuscleVolumeReport,
    Period,
    VolumeReport,
)
from app.services.stats import StatsService, resolve_range
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError


def _window(
    period: Period | None, date_from: date | None, date_to: date | None
) -> StatsRange:
    try:
        return resolve_range(period, date_from, date_to)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def volume(
    ctx: Context,
    period: Period | None = Period.WEEK_4,
    date_from: date | None = None,
    date_to: date | None = None,
    bucket: Bucket = Bucket.WEEK,
) -> VolumeReport:
    """Tonnage, sets and reps over a window, bucketed. Explicit dates override
    `period`; missing buckets are zero-filled."""
    async with authenticated_session(ctx) as (session, user):
        service = StatsService(session)
        return await service.volume(
            user.id, _window(period, date_from, date_to), bucket
        )


@mcp.tool()
async def by_muscle(
    ctx: Context,
    period: Period | None = Period.WEEK_4,
    date_from: date | None = None,
    date_to: date | None = None,
    role: MuscleRoleFilter = MuscleRoleFilter.PRIMARY,
) -> MuscleVolumeReport:
    """Volume per muscle group over a window. Totals do not sum to the range
    total on purpose — one exercise counts for every muscle it maps to."""
    async with authenticated_session(ctx) as (session, user):
        service = StatsService(session)
        return await service.by_muscle(
            user.id, _window(period, date_from, date_to), role.to_role()
        )


@mcp.tool()
async def by_exercise(
    ctx: Context,
    period: Period | None = Period.WEEK_4,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 20,
) -> ExerciseVolumeReport:
    """Top exercises by tonnage over a window."""
    async with authenticated_session(ctx) as (session, user):
        service = StatsService(session)
        return await service.by_exercise(
            user.id, _window(period, date_from, date_to), limit=limit
        )
