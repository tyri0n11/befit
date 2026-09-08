"""Two-way sync between PLANNED workout sessions and one Google Calendar per
user. Raw `httpx` calls against the Calendar v3 REST API, matching the
project's preference for direct REST over provider SDKs (see the rationale
for Resend in app/services/email.py / CLAUDE.md) — no new dependency.

Sync is inline: TrainingService calls `sync_session`/`remove_session`
directly in the request path (app/services/training.py), so a Calendar
outage can fail a session write. Accepted trade-off — see the feature plan.

Two-way is scoped to events *this app created*: `pull_changes` only acts on
events that have a `session_calendar_events` mapping. An event created
directly in Google Calendar has no reliable mapping to exercises, so it is
ignored rather than guessed into a new session.

The watch channel Google uses for push notifications expires (~7 days) and
there is no scheduler in this app to renew it proactively; `sync_session`
opportunistically renews it when it is close to expiry, piggybacking on
whatever request triggered the call. A user who never touches sessions will
have a stale channel — a known limitation, not solved here.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.models.calendar import CalendarConnection, SessionCalendarEvent
from app.models.training import SessionStatus, WorkoutSession
from app.repositories.calendar import CalendarRepository
from app.services.google_oauth import CalendarTokens

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/calendar/v3"
_REQUEST_TIMEOUT = httpx.Timeout(10.0)
# Renew the watch channel once less than this remains, rather than waiting
# for it to lapse and silently stop delivering pull-direction changes.
_CHANNEL_RENEW_WINDOW = timedelta(days=1)
# Google requires this even for a permanent channel; renew before it expires.
_CHANNEL_TTL = timedelta(days=7)


class GoogleCalendarError(Exception):
    """Message is safe to show a caller."""


@dataclass(frozen=True)
class PulledChange:
    """One session-affecting change pulled from Google Calendar. The caller
    (the webhook endpoint) applies it through TrainingService — this module
    never imports TrainingService, so push and pull stay decoupled and there
    is no import cycle between the two services."""

    user_id: int
    session_id: int
    deleted: bool
    new_date: date | None = None


def _expiry(seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


class GoogleCalendarService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = CalendarRepository(session)

    # --- connect / disconnect ------------------------------------------

    async def connect(self, tokens: CalendarTokens) -> None:
        connection = await self.repo.get_connection(tokens.user_id)
        if connection is None:
            connection = CalendarConnection(user_id=tokens.user_id)
            self.repo.add_connection(connection)

        connection.access_token = tokens.access_token
        connection.refresh_token = tokens.refresh_token
        connection.token_expires_at = _expiry(tokens.expires_in)
        connection.scope = tokens.scope
        await self.repo.flush()

        await self._register_watch_channel(connection)
        await self.repo.flush()

    async def disconnect(self, user_id: int) -> None:
        connection = await self.repo.get_connection(user_id)
        if connection is None:
            return
        if connection.channel_id is not None:
            await self._stop_channel(connection)
        await self.repo.delete_connection(connection)

    async def status(self, user_id: int) -> CalendarConnection | None:
        return await self.repo.get_connection(user_id)

    # --- push: session -> calendar ---------------------------------------

    async def sync_session(self, user_id: int, session: WorkoutSession) -> None:
        connection = await self.repo.get_connection(user_id)
        if connection is None:
            return

        mapping = await self.repo.get_mapping(session.id)
        access_token = await self._valid_access_token(connection)

        if session.status == SessionStatus.PLANNED:
            if mapping is None:
                event_id = await self._insert_event(connection, access_token, session)
                self.repo.add_mapping(
                    SessionCalendarEvent(
                        session_id=session.id,
                        user_id=user_id,
                        google_event_id=event_id,
                    )
                )
            else:
                await self._patch_event(
                    connection, access_token, mapping.google_event_id, session
                )
        elif mapping is not None:
            # Left PLANNED — the calendar reflects what's still ahead, not
            # training history (which stays in the app itself).
            await self._delete_event(connection, access_token, mapping.google_event_id)
            await self.repo.delete_mapping(mapping)

        await self._maybe_renew_channel(connection, access_token)
        await self.repo.flush()

    async def remove_session(self, user_id: int, session_id: int) -> None:
        connection = await self.repo.get_connection(user_id)
        if connection is None:
            return
        mapping = await self.repo.get_mapping(session_id)
        if mapping is None:
            return
        access_token = await self._valid_access_token(connection)
        await self._delete_event(connection, access_token, mapping.google_event_id)
        await self.repo.delete_mapping(mapping)

    # --- pull: calendar -> session -----------------------------------------

    async def pull_changes(self, channel_id: uuid.UUID) -> list[PulledChange]:
        connection = await self.repo.get_connection_by_channel(channel_id)
        if connection is None:
            return []

        access_token = await self._valid_access_token(connection)
        events, next_sync_token = await self._list_changed_events(
            connection, access_token
        )
        connection.sync_token = next_sync_token
        await self.repo.flush()

        changes: list[PulledChange] = []
        for event in events:
            mapping = await self.repo.get_mapping_by_event(
                connection.user_id, event["id"]
            )
            if mapping is None:
                # Not an event we created — out of scope, see module docstring.
                continue

            if event.get("status") == "cancelled":
                changes.append(
                    PulledChange(
                        user_id=connection.user_id,
                        session_id=mapping.session_id,
                        deleted=True,
                    )
                )
                continue

            new_date_str = event.get("start", {}).get("date")
            if new_date_str:
                new_date = date.fromisoformat(new_date_str)
                changes.append(
                    PulledChange(
                        user_id=connection.user_id,
                        session_id=mapping.session_id,
                        deleted=False,
                        new_date=new_date,
                    )
                )
        return changes

    # --- token refresh -------------------------------------------------

    async def _valid_access_token(self, connection: CalendarConnection) -> str:
        if connection.token_expires_at > datetime.now(UTC) + timedelta(minutes=1):
            return connection.access_token

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "refresh_token": connection.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        if response.is_error:
            logger.error(
                "google calendar token refresh failed for user %s: %s %s",
                connection.user_id,
                response.status_code,
                response.text,
            )
            raise GoogleCalendarError("Google Calendar access could not be refreshed")

        body = response.json()
        connection.access_token = body["access_token"]
        connection.token_expires_at = _expiry(int(body.get("expires_in", 3600)))
        await self.repo.flush()
        return connection.access_token

    # --- watch channel ---------------------------------------------------

    async def _register_watch_channel(self, connection: CalendarConnection) -> None:
        channel_id = uuid.uuid4()
        access_token = await self._valid_access_token(connection)
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                f"{API_BASE}/calendars/{connection.calendar_id}/events/watch",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "id": str(channel_id),
                    "type": "web_hook",
                    "address": settings.GOOGLE_CALENDAR_WEBHOOK_URL,
                    "token": settings.GOOGLE_CALENDAR_WEBHOOK_TOKEN,
                    # Google may cap this lower than requested; we still
                    # track our own TTL as the renewal trigger.
                    "expiration": str(
                        int((datetime.now(UTC) + _CHANNEL_TTL).timestamp() * 1000)
                    ),
                },
            )
        if response.is_error:
            logger.error(
                "google calendar watch registration failed for user %s: %s %s",
                connection.user_id,
                response.status_code,
                response.text,
            )
            raise GoogleCalendarError("Could not subscribe to Google Calendar changes")

        body = response.json()
        connection.channel_id = channel_id
        connection.resource_id = body["resourceId"]
        connection.channel_expires_at = _expiry_from_ms(body.get("expiration"))

    async def _stop_channel(self, connection: CalendarConnection) -> None:
        access_token = await self._valid_access_token(connection)
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                f"{API_BASE}/channels/stop",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "id": str(connection.channel_id),
                    "resourceId": connection.resource_id,
                },
            )
        # Best-effort: a stale/already-expired channel 404s, which is fine —
        # we're disconnecting either way.
        if response.is_error and response.status_code != 404:
            logger.error(
                "google calendar channel stop failed for user %s: %s %s",
                connection.user_id,
                response.status_code,
                response.text,
            )

    async def _maybe_renew_channel(
        self, connection: CalendarConnection, access_token: str
    ) -> None:
        if (
            connection.channel_expires_at is not None
            and connection.channel_expires_at - datetime.now(UTC)
            > _CHANNEL_RENEW_WINDOW
        ):
            return
        if connection.channel_id is not None:
            await self._stop_channel(connection)
        await self._register_watch_channel(connection)

    # --- events ------------------------------------------------------------

    def _event_body(self, session: WorkoutSession) -> dict:
        start = session.session_date
        end = start + timedelta(days=1)
        return {
            "summary": session.program_day or "Planned workout",
            "description": session.notes or "",
            "start": {"date": start.isoformat()},
            "end": {"date": end.isoformat()},
        }

    async def _insert_event(
        self, connection: CalendarConnection, access_token: str, session: WorkoutSession
    ) -> str:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                f"{API_BASE}/calendars/{connection.calendar_id}/events",
                headers={"Authorization": f"Bearer {access_token}"},
                json=self._event_body(session),
            )
        if response.is_error:
            logger.error(
                "google calendar event insert failed for session %s: %s %s",
                session.id,
                response.status_code,
                response.text,
            )
            raise GoogleCalendarError("Could not create the calendar event")
        return response.json()["id"]

    async def _patch_event(
        self,
        connection: CalendarConnection,
        access_token: str,
        google_event_id: str,
        session: WorkoutSession,
    ) -> None:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.patch(
                f"{API_BASE}/calendars/{connection.calendar_id}/events/{google_event_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                json=self._event_body(session),
            )
        if response.is_error:
            logger.error(
                "google calendar event patch failed for session %s: %s %s",
                session.id,
                response.status_code,
                response.text,
            )
            raise GoogleCalendarError("Could not update the calendar event")

    async def _delete_event(
        self, connection: CalendarConnection, access_token: str, google_event_id: str
    ) -> None:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.delete(
                f"{API_BASE}/calendars/{connection.calendar_id}/events/{google_event_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        # Already gone (deleted in Calendar directly) is fine, not an error.
        if response.is_error and response.status_code not in (404, 410):
            logger.error(
                "google calendar event delete failed for event %s: %s %s",
                google_event_id,
                response.status_code,
                response.text,
            )
            raise GoogleCalendarError("Could not remove the calendar event")

    async def _list_changed_events(
        self, connection: CalendarConnection, access_token: str
    ) -> tuple[list[dict], str | None]:
        # syncToken only belongs on the first page of a paged sync — Google
        # rejects it alongside pageToken — but showDeleted must ride along on
        # every page or a later page could hide a cancellation.
        base_params = {"showDeleted": "true"}
        if connection.sync_token:
            base_params["syncToken"] = connection.sync_token

        events: list[dict] = []
        next_sync_token = connection.sync_token
        page_token: str | None = None
        url = f"{API_BASE}/calendars/{connection.calendar_id}/events"
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            while True:
                params = (
                    {"showDeleted": "true", "pageToken": page_token}
                    if page_token
                    else base_params
                )
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                )
                if response.is_error:
                    if response.status_code == 410:
                        # Sync token expired/invalidated — full resync next
                        # time; nothing usable to apply now.
                        return [], None
                    logger.error(
                        "google calendar events.list failed for user %s: %s %s",
                        connection.user_id,
                        response.status_code,
                        response.text,
                    )
                    raise GoogleCalendarError("Could not read Google Calendar changes")

                body = response.json()
                events.extend(body.get("items", []))
                next_sync_token = body.get("nextSyncToken", next_sync_token)
                page_token = body.get("nextPageToken")
                if not page_token:
                    break

        return events, next_sync_token


def _expiry_from_ms(expiration_ms: str | None) -> datetime | None:
    if not expiration_ms:
        return None
    return datetime.fromtimestamp(int(expiration_ms) / 1000, tz=UTC)
