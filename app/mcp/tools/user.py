"""User profile + body metrics tools — auth required, mirrors
app/api/v1/endpoints/user.py and app/api/v1/endpoints/body_metrics.py.
"""

from datetime import date

from app.mcp.context import authenticated_session
from app.mcp.server import mcp
from app.repositories.stats import StatsRange
from app.schemas.body_metrics import (
    BodyMetricsLogCreate,
    BodyMetricsLogPage,
    BodyMetricsLogResponse,
    BodyMetricsProgress,
)
from app.schemas.stats import Period
from app.schemas.user import UserProfileResponse, UserProfileUpdate
from app.services.body_metrics import BodyMetricsError, BodyMetricsService
from app.services.stats import resolve_range
from app.services.user_profile import UserProfileService
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
async def get_profile(ctx: Context) -> UserProfileResponse:
    """Physical profile — created empty on first read."""
    async with authenticated_session(ctx) as (session, user):
        service = UserProfileService(session)
        profile = await service.get(user.id)
        return UserProfileResponse.model_validate(profile)


@mcp.tool()
async def update_profile(
    payload: UserProfileUpdate, ctx: Context
) -> UserProfileResponse:
    """Update the profile; bmr_kcal/tdee_kcal are recomputed, not settable."""
    async with authenticated_session(ctx) as (session, user):
        service = UserProfileService(session)
        profile = await service.update(user.id, payload)
        return UserProfileResponse.model_validate(profile)


@mcp.tool()
async def log_body_metrics(
    payload: BodyMetricsLogCreate, ctx: Context
) -> BodyMetricsLogResponse:
    """Log weight/body-fat/muscle-mass for a day. Logging again for a day
    already logged replaces that day's entry rather than adding a second one."""
    async with authenticated_session(ctx) as (session, user):
        service = BodyMetricsService(session)
        entry = await service.log(user.id, payload)
        return BodyMetricsLogResponse.model_validate(entry)


@mcp.tool()
async def list_body_metrics(
    ctx: Context,
    period: Period | None = Period.WEEK_4,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> BodyMetricsLogPage:
    """List logged body-metrics entries, most recent first."""
    async with authenticated_session(ctx) as (session, user):
        service = BodyMetricsService(session)
        window = _window(period, date_from, date_to)
        items, total = await service.list_page(
            user.id, window, limit=limit, offset=offset
        )
        return BodyMetricsLogPage(
            items=[BodyMetricsLogResponse.model_validate(e) for e in items],
            total=total,
            limit=limit,
            offset=offset,
        )


@mcp.tool()
async def body_metrics_progress(
    ctx: Context,
    period: Period | None = Period.WEEK_4,
    date_from: date | None = None,
    date_to: date | None = None,
) -> BodyMetricsProgress:
    """Baseline vs. current and the change between them, per metric, over a window."""
    async with authenticated_session(ctx) as (session, user):
        service = BodyMetricsService(session)
        return await service.progress(user.id, _window(period, date_from, date_to))


@mcp.tool()
async def delete_body_metrics_entry(log_id: int, ctx: Context) -> None:
    """Delete a logged body-metrics entry."""
    async with authenticated_session(ctx) as (session, user):
        service = BodyMetricsService(session)
        try:
            await service.delete(user.id, log_id)
        except BodyMetricsError as exc:
            raise ToolError(f"{exc.code.value}: {exc.message}") from exc
