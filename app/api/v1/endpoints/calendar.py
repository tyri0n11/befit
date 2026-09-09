"""Two-way Google Calendar sync for planned workout sessions. See
app/services/google_calendar.py for the mechanics and its module docstring
for the push/pull scoping."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis

from app.api.v1.dependencies import (
    CurrentUser,
    get_auth_service,
    get_calendar_service,
    get_training_service,
)
from app.core.redis import get_redis
from app.core.settings import settings
from app.schemas.calendar import CalendarStatusResponse
from app.schemas.training import WorkoutSessionUpdate
from app.services.auth import AuthError, AuthService
from app.services.google_calendar import GoogleCalendarError, GoogleCalendarService
from app.services.google_oauth import (
    GoogleOAuthError,
    complete_calendar_connect,
    start_calendar_connect,
)
from app.services.training import TrainingError, TrainingService

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get(
    "/status",
    response_model=CalendarStatusResponse,
    summary="Whether Google Calendar is connected",
)
async def calendar_status(
    user: CurrentUser,
    service: GoogleCalendarService = Depends(get_calendar_service),
) -> CalendarStatusResponse:
    connection = await service.status(user.id)
    if connection is None:
        return CalendarStatusResponse(connected=False)
    return CalendarStatusResponse(
        connected=True,
        calendar_id=connection.calendar_id,
        connected_at=connection.created_at,
    )


@router.get(
    "/connect",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    summary="Redirect to Google consent for Calendar access",
)
async def connect(
    access_token: str,
    redis: Redis = Depends(get_redis),
    auth_service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    """Auth via a query-param access token rather than `CurrentUser`
    (`Authorization` header): this endpoint is meant to be opened directly in
    a browser (`expo-web-browser`'s `openAuthSessionAsync` on the mobile side
    — see befit-mobile's CalendarService), which cannot attach a header to a
    plain navigation. Scoped to this endpoint only; every other authenticated
    route still requires the header. The token is short-lived
    (`ACCESS_TOKEN_EXPIRE_MINUTES`), same exposure window a URL-based OAuth
    `code` already has."""
    try:
        user = await auth_service.user_from_access_token(access_token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message
        ) from exc

    try:
        url, _state = await start_calendar_connect(redis, user.id)
    except GoogleOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return RedirectResponse(url)


@router.get(
    "/callback",
    response_model=None,
    summary="Complete the Calendar connect flow",
)
async def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    redis: Redis = Depends(get_redis),
    service: GoogleCalendarService = Depends(get_calendar_service),
) -> RedirectResponse:
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Calendar connect was not completed ({error})",
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code or state"
        )

    try:
        tokens = await complete_calendar_connect(redis, code, state)
    except GoogleOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    try:
        await service.connect(tokens)
    except GoogleCalendarError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    # Calendar connect is always initiated from the app, unlike
    # /auth/google/callback which also serves a browser client directly — no
    # JSON branch needed here. Shares the login flow's deep link
    # (MOBILE_APP_SCHEME) but marks it with ?calendar=connected so
    # GoogleCallbackScreen can tell the two apart without touching tokens.
    return RedirectResponse(
        f"{settings.MOBILE_APP_SCHEME}?calendar=connected",
        status_code=status.HTTP_302_FOUND,
    )


@router.delete(
    "/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect Google Calendar",
)
async def disconnect(
    user: CurrentUser,
    service: GoogleCalendarService = Depends(get_calendar_service),
) -> Response:
    await service.disconnect(user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Google Calendar push notification receiver",
)
async def webhook(
    x_goog_channel_id: str | None = Header(None),
    x_goog_resource_state: str | None = Header(None),
    x_goog_channel_token: str | None = Header(None),
    calendar_service: GoogleCalendarService = Depends(get_calendar_service),
    training_service: TrainingService = Depends(get_training_service),
) -> Response:
    if x_goog_channel_token != settings.GOOGLE_CALENDAR_WEBHOOK_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid channel token"
        )
    if not x_goog_channel_id or x_goog_resource_state == "sync":
        # "sync" is Google's initial handshake when the channel is created —
        # nothing has changed yet.
        return Response(status_code=status.HTTP_200_OK)

    try:
        channel_id = uuid.UUID(x_goog_channel_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed channel id"
        ) from None

    try:
        changes = await calendar_service.pull_changes(channel_id)
    except GoogleCalendarError:
        # Google expects a fast 2xx or it retries with backoff; there is no
        # caller to report an error to, so just let the retry happen.
        return Response(status_code=status.HTTP_200_OK)

    for change in changes:
        try:
            if change.deleted:
                await training_service.delete_session(change.user_id, change.session_id)
            elif change.new_date is not None:
                await training_service.update_session(
                    change.user_id,
                    change.session_id,
                    WorkoutSessionUpdate(session_date=change.new_date),
                )
        except TrainingError:
            # The session may already have been deleted/purged on our side
            # since the event was created — nothing to apply, move on.
            continue

    return Response(status_code=status.HTTP_200_OK)
