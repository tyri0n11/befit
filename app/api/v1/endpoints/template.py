from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.v1.dependencies import CurrentUser, get_template_service
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
from app.services.template import TemplateError, TemplateErrorCode, TemplateService

router = APIRouter(prefix="/templates", tags=["templates"])

# Someone else's template is a 404, not a 403 — see the module docstring of
# app/services/template.py.
_STATUS = {
    TemplateErrorCode.TEMPLATE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    TemplateErrorCode.TEMPLATE_EXERCISE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    TemplateErrorCode.SESSION_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    TemplateErrorCode.UNKNOWN_EXERCISE: status.HTTP_422_UNPROCESSABLE_CONTENT,
    TemplateErrorCode.ORDER_TAKEN: status.HTTP_409_CONFLICT,
    TemplateErrorCode.NAME_TAKEN: status.HTTP_409_CONFLICT,
}


def _http(exc: TemplateError) -> HTTPException:
    return HTTPException(status_code=_STATUS[exc.code], detail=exc.message)


@router.get("", response_model=WorkoutTemplatePage, summary="List my templates")
async def list_templates(
    user: CurrentUser,
    q: str | None = Query(None, max_length=100, description="Match the name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: TemplateService = Depends(get_template_service),
) -> WorkoutTemplatePage:
    items, total = await service.list(user.id, q, limit=limit, offset=offset)
    return WorkoutTemplatePage(
        items=[WorkoutTemplateSummary.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=WorkoutTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a template, optionally with its exercises",
)
async def create_template(
    payload: WorkoutTemplateCreate,
    user: CurrentUser,
    service: TemplateService = Depends(get_template_service),
) -> WorkoutTemplateResponse:
    try:
        template = await service.create(user.id, payload)
    except TemplateError as exc:
        raise _http(exc) from exc
    return WorkoutTemplateResponse.model_validate(template)


@router.post(
    "/from-session/{session_id}",
    response_model=WorkoutTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a session's plan as a reusable template",
)
async def template_from_session(
    session_id: int,
    payload: TemplateFromSession,
    user: CurrentUser,
    service: TemplateService = Depends(get_template_service),
) -> WorkoutTemplateResponse:
    try:
        template = await service.from_session(user.id, session_id, payload)
    except TemplateError as exc:
        raise _http(exc) from exc
    return WorkoutTemplateResponse.model_validate(template)


@router.get(
    "/{template_id}",
    response_model=WorkoutTemplateResponse,
    summary="One template with its exercises",
)
async def get_template(
    template_id: int,
    user: CurrentUser,
    service: TemplateService = Depends(get_template_service),
) -> WorkoutTemplateResponse:
    try:
        template = await service.get(user.id, template_id)
    except TemplateError as exc:
        raise _http(exc) from exc
    return WorkoutTemplateResponse.model_validate(template)


@router.patch(
    "/{template_id}",
    response_model=WorkoutTemplateResponse,
    summary="Rename a template or change its notes",
)
async def update_template(
    template_id: int,
    payload: WorkoutTemplateUpdate,
    user: CurrentUser,
    service: TemplateService = Depends(get_template_service),
) -> WorkoutTemplateResponse:
    try:
        template = await service.update(user.id, template_id, payload)
    except TemplateError as exc:
        raise _http(exc) from exc
    return WorkoutTemplateResponse.model_validate(template)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a template; sessions made from it are untouched",
)
async def delete_template(
    template_id: int,
    user: CurrentUser,
    service: TemplateService = Depends(get_template_service),
) -> Response:
    try:
        await service.delete(user.id, template_id)
    except TemplateError as exc:
        raise _http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{template_id}/sessions",
    response_model=WorkoutSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a session from this template",
)
async def instantiate(
    template_id: int,
    payload: TemplateInstantiate,
    user: CurrentUser,
    service: TemplateService = Depends(get_template_service),
) -> WorkoutSessionResponse:
    try:
        workout = await service.instantiate(user.id, template_id, payload)
    except TemplateError as exc:
        raise _http(exc) from exc
    return WorkoutSessionResponse.model_validate(workout)


@router.post(
    "/{template_id}/exercises",
    response_model=TemplateExerciseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an exercise to a template",
)
async def add_exercise(
    template_id: int,
    payload: TemplateExerciseCreate,
    user: CurrentUser,
    service: TemplateService = Depends(get_template_service),
) -> TemplateExerciseResponse:
    try:
        item = await service.add_exercise(user.id, template_id, payload)
    except TemplateError as exc:
        raise _http(exc) from exc
    return TemplateExerciseResponse.model_validate(item)


@router.patch(
    "/{template_id}/exercises/{template_exercise_id}",
    response_model=TemplateExerciseResponse,
    summary="Update one exercise's targets or order",
)
async def update_exercise(
    template_id: int,
    template_exercise_id: int,
    payload: TemplateExerciseUpdate,
    user: CurrentUser,
    service: TemplateService = Depends(get_template_service),
) -> TemplateExerciseResponse:
    try:
        item = await service.update_exercise(
            user.id, template_id, template_exercise_id, payload
        )
    except TemplateError as exc:
        raise _http(exc) from exc
    return TemplateExerciseResponse.model_validate(item)


@router.delete(
    "/{template_id}/exercises/{template_exercise_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an exercise from a template",
)
async def delete_exercise(
    template_id: int,
    template_exercise_id: int,
    user: CurrentUser,
    service: TemplateService = Depends(get_template_service),
) -> Response:
    try:
        await service.delete_exercise(user.id, template_id, template_exercise_id)
    except TemplateError as exc:
        raise _http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
