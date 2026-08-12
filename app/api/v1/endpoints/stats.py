from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.dependencies import CurrentUser, get_stats_service
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

router = APIRouter(prefix="/stats", tags=["stats"])

_PERIOD = Query(
    Period.WEEK_4, description="Window counted back from today; ignored if date_from"
)
_FROM = Query(None, description="Overrides `period`")
_TO = Query(None, description="Defaults to today")


def _window(
    period: Period | None, date_from: date | None, date_to: date | None
) -> StatsRange:
    try:
        return resolve_range(period, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.get(
    "/volume",
    response_model=VolumeReport,
    summary="Tonnage, sets and reps over a window, bucketed",
)
async def volume(
    user: CurrentUser,
    period: Period | None = _PERIOD,
    date_from: date | None = _FROM,
    date_to: date | None = _TO,
    bucket: Bucket = Bucket.WEEK,
    service: StatsService = Depends(get_stats_service),
) -> VolumeReport:
    return await service.volume(user.id, _window(period, date_from, date_to), bucket)


@router.get(
    "/muscles",
    response_model=MuscleVolumeReport,
    summary="Volume per muscle group over a window",
)
async def by_muscle(
    user: CurrentUser,
    period: Period | None = _PERIOD,
    date_from: date | None = _FROM,
    date_to: date | None = _TO,
    role: MuscleRoleFilter = Query(
        MuscleRoleFilter.PRIMARY,
        description="`all` counts every role an exercise maps to",
    ),
    service: StatsService = Depends(get_stats_service),
) -> MuscleVolumeReport:
    return await service.by_muscle(
        user.id, _window(period, date_from, date_to), role.to_role()
    )


@router.get(
    "/exercises",
    response_model=ExerciseVolumeReport,
    summary="Top exercises by tonnage over a window",
)
async def by_exercise(
    user: CurrentUser,
    period: Period | None = _PERIOD,
    date_from: date | None = _FROM,
    date_to: date | None = _TO,
    limit: int = Query(20, ge=1, le=100),
    service: StatsService = Depends(get_stats_service),
) -> ExerciseVolumeReport:
    return await service.by_exercise(
        user.id, _window(period, date_from, date_to), limit=limit
    )
