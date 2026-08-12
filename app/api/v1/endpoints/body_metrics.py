from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.v1.dependencies import CurrentUser, get_body_metrics_service
from app.schemas.body_metrics import (
    BodyMetricsLogCreate,
    BodyMetricsLogPage,
    BodyMetricsLogResponse,
    BodyMetricsProgress,
)
from app.schemas.stats import Period
from app.services.body_metrics import BodyMetricsError, BodyMetricsService
from app.services.stats import resolve_range

router = APIRouter(prefix="/users/me/body-metrics", tags=["body-metrics"])

_STATUS = {"log_not_found": status.HTTP_404_NOT_FOUND}

_PERIOD = Query(
    Period.WEEK_4, description="Window counted back from today; ignored if date_from"
)
_FROM = Query(None, description="Overrides `period`")
_TO = Query(None, description="Defaults to today")


def _window(period: Period | None, date_from: date | None, date_to: date | None):
    try:
        return resolve_range(period, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


def _http(exc: BodyMetricsError) -> HTTPException:
    return HTTPException(status_code=_STATUS[exc.code.value], detail=exc.message)


@router.post(
    "",
    response_model=BodyMetricsLogResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Log today's (or a given day's) weight/body-fat/muscle-mass",
)
async def log_metrics(
    payload: BodyMetricsLogCreate,
    user: CurrentUser,
    service: BodyMetricsService = Depends(get_body_metrics_service),
) -> BodyMetricsLogResponse:
    entry = await service.log(user.id, payload)
    return BodyMetricsLogResponse.model_validate(entry)


@router.get("", response_model=BodyMetricsLogPage, summary="List logged entries")
async def list_metrics(
    user: CurrentUser,
    period: Period | None = _PERIOD,
    date_from: date | None = _FROM,
    date_to: date | None = _TO,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: BodyMetricsService = Depends(get_body_metrics_service),
) -> BodyMetricsLogPage:
    window = _window(period, date_from, date_to)
    items, total = await service.list_page(user.id, window, limit=limit, offset=offset)
    return BodyMetricsLogPage(
        items=[BodyMetricsLogResponse.model_validate(e) for e in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/progress",
    response_model=BodyMetricsProgress,
    summary="Baseline vs. current and the change between them, per metric",
)
async def get_progress(
    user: CurrentUser,
    period: Period | None = _PERIOD,
    date_from: date | None = _FROM,
    date_to: date | None = _TO,
    service: BodyMetricsService = Depends(get_body_metrics_service),
) -> BodyMetricsProgress:
    window = _window(period, date_from, date_to)
    return await service.progress(user.id, window)


@router.delete(
    "/{log_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a logged entry",
)
async def delete_metrics(
    log_id: int,
    user: CurrentUser,
    service: BodyMetricsService = Depends(get_body_metrics_service),
) -> Response:
    try:
        await service.delete(user.id, log_id)
    except BodyMetricsError as exc:
        raise _http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
