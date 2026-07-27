from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.v1.dependencies import CurrentUser, get_training_service
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
from app.services.training import TrainingError, TrainingErrorCode, TrainingService

router = APIRouter(prefix="/sessions", tags=["training"])

# A session belonging to someone else is a 404, not a 403 — see the module
# docstring of app/services/training.py.
_STATUS = {
    TrainingErrorCode.SESSION_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    TrainingErrorCode.SESSION_EXERCISE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    TrainingErrorCode.SET_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    TrainingErrorCode.UNKNOWN_EXERCISE: status.HTTP_422_UNPROCESSABLE_CONTENT,
    TrainingErrorCode.ORDER_TAKEN: status.HTTP_409_CONFLICT,
    TrainingErrorCode.SET_INDEX_TAKEN: status.HTTP_409_CONFLICT,
}


def _http(exc: TrainingError) -> HTTPException:
    return HTTPException(status_code=_STATUS[exc.code], detail=exc.message)


@router.get("", response_model=WorkoutSessionPage, summary="List my sessions")
async def list_sessions(
    user: CurrentUser,
    status_: SessionStatus | None = Query(None, alias="status"),
    date_from: date | None = None,
    date_to: date | None = None,
    program_day: str | None = Query(None, max_length=64),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: TrainingService = Depends(get_training_service),
) -> WorkoutSessionPage:
    filters = SessionFilters(
        status=status_,
        date_from=date_from,
        date_to=date_to,
        program_day=program_day,
    )
    return await service.list_sessions(user.id, filters, limit=limit, offset=offset)


@router.post(
    "",
    response_model=WorkoutSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a session, optionally with its planned exercises",
)
async def create_session(
    payload: WorkoutSessionCreate,
    user: CurrentUser,
    service: TrainingService = Depends(get_training_service),
) -> WorkoutSessionResponse:
    try:
        workout = await service.create_session(user.id, payload)
    except TrainingError as exc:
        raise _http(exc) from exc
    return WorkoutSessionResponse.model_validate(workout)


@router.get(
    "/{session_id}",
    response_model=WorkoutSessionResponse,
    summary="One session with its exercises and sets",
)
async def get_session(
    session_id: int,
    user: CurrentUser,
    service: TrainingService = Depends(get_training_service),
) -> WorkoutSessionResponse:
    try:
        workout = await service.get_session(user.id, session_id)
    except TrainingError as exc:
        raise _http(exc) from exc
    return WorkoutSessionResponse.model_validate(workout)


@router.patch(
    "/{session_id}",
    response_model=WorkoutSessionResponse,
    summary="Update a session's status, bodyweight or notes",
)
async def update_session(
    session_id: int,
    payload: WorkoutSessionUpdate,
    user: CurrentUser,
    service: TrainingService = Depends(get_training_service),
) -> WorkoutSessionResponse:
    try:
        workout = await service.update_session(user.id, session_id, payload)
    except TrainingError as exc:
        raise _http(exc) from exc
    return WorkoutSessionResponse.model_validate(workout)


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session and everything logged under it",
)
async def delete_session(
    session_id: int,
    user: CurrentUser,
    service: TrainingService = Depends(get_training_service),
) -> Response:
    try:
        await service.delete_session(user.id, session_id)
    except TrainingError as exc:
        raise _http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{session_id}/exercises",
    response_model=SessionExerciseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an exercise to a session",
)
async def add_exercise(
    session_id: int,
    payload: SessionExerciseCreate,
    user: CurrentUser,
    service: TrainingService = Depends(get_training_service),
) -> SessionExerciseResponse:
    try:
        item = await service.add_exercise(user.id, session_id, payload)
    except TrainingError as exc:
        raise _http(exc) from exc
    return SessionExerciseResponse.model_validate(item)


@router.patch(
    "/{session_id}/exercises/{session_exercise_id}",
    response_model=SessionExerciseResponse,
    summary="Update targets, order or status of one exercise",
)
async def update_exercise(
    session_id: int,
    session_exercise_id: int,
    payload: SessionExerciseUpdate,
    user: CurrentUser,
    service: TrainingService = Depends(get_training_service),
) -> SessionExerciseResponse:
    try:
        item = await service.update_exercise(
            user.id, session_id, session_exercise_id, payload
        )
    except TrainingError as exc:
        raise _http(exc) from exc
    return SessionExerciseResponse.model_validate(item)


@router.delete(
    "/{session_id}/exercises/{session_exercise_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an exercise and its sets from a session",
)
async def delete_exercise(
    session_id: int,
    session_exercise_id: int,
    user: CurrentUser,
    service: TrainingService = Depends(get_training_service),
) -> Response:
    try:
        await service.delete_exercise(user.id, session_id, session_exercise_id)
    except TrainingError as exc:
        raise _http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{session_id}/exercises/{session_exercise_id}/sets",
    response_model=SetLogResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Log a set",
)
async def log_set(
    session_id: int,
    session_exercise_id: int,
    payload: SetLogCreate,
    user: CurrentUser,
    service: TrainingService = Depends(get_training_service),
) -> SetLogResponse:
    try:
        log = await service.log_set(user.id, session_id, session_exercise_id, payload)
    except TrainingError as exc:
        raise _http(exc) from exc
    return SetLogResponse.model_validate(log)


@router.patch(
    "/{session_id}/exercises/{session_exercise_id}/sets/{set_id}",
    response_model=SetLogResponse,
    summary="Correct a logged set",
)
async def update_set(
    session_id: int,
    session_exercise_id: int,
    set_id: int,
    payload: SetLogUpdate,
    user: CurrentUser,
    service: TrainingService = Depends(get_training_service),
) -> SetLogResponse:
    try:
        log = await service.update_set(user.id, session_exercise_id, set_id, payload)
    except TrainingError as exc:
        raise _http(exc) from exc
    return SetLogResponse.model_validate(log)


@router.delete(
    "/{session_id}/exercises/{session_exercise_id}/sets/{set_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a logged set",
)
async def delete_set(
    session_id: int,
    session_exercise_id: int,
    set_id: int,
    user: CurrentUser,
    service: TrainingService = Depends(get_training_service),
) -> Response:
    try:
        await service.delete_set(user.id, session_exercise_id, set_id)
    except TrainingError as exc:
        raise _http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
