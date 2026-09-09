"""Tests for Google Calendar sync. Nothing contacts Google: every httpx call
GoogleCalendarService would make lives behind small private methods
(_insert_event, _patch_event, _delete_event, _valid_access_token,
_maybe_renew_channel, _list_changed_events) that these tests monkeypatch at
the class level — the same seam-mocking approach test_auth.py uses for
complete_login on the mobile-callback tests, rather than mocking httpx
transport. See conftest.py for the transactional session fixture.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.models.calendar import CalendarConnection
from app.models.training import SessionStatus
from app.models.user import User
from app.schemas.training import WorkoutSessionCreate, WorkoutSessionUpdate
from app.services.google_calendar import GoogleCalendarService
from app.services.training import TrainingService

BASE = f"{settings.API_V1_STR}/calendar"
AUTH = f"{settings.API_V1_STR}/auth"
TODAY = date.today()

CREDENTIALS = {
    "email": "calendar-user@example.com",
    "display_name": "Calendar User",
    "password": "correct-horse-battery",
}


@pytest_asyncio.fixture
async def user(session: AsyncSession) -> User:
    """A real users row — calendar_connections.user_id is a real FK."""
    u = User(email=CREDENTIALS["email"], display_name=CREDENTIALS["display_name"])
    session.add(u)
    await session.flush()
    return u


@pytest_asyncio.fixture
async def calendar_connection(session: AsyncSession, user: User) -> CalendarConnection:
    conn = CalendarConnection(
        user_id=user.id,
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        scope="openid email profile https://www.googleapis.com/auth/calendar.events",
        channel_id=uuid4(),
        resource_id="test-resource-id",
        channel_expires_at=datetime.now(UTC) + timedelta(days=6),
    )
    session.add(conn)
    await session.flush()
    return conn


@dataclass
class GoogleMocks:
    insert_event: AsyncMock
    patch_event: AsyncMock
    delete_event: AsyncMock
    list_changed_events: AsyncMock


@pytest_asyncio.fixture
def google_mocks(monkeypatch: pytest.MonkeyPatch) -> GoogleMocks:
    mocks = GoogleMocks(
        insert_event=AsyncMock(side_effect=lambda *a, **k: f"gcal-{uuid4().hex[:8]}"),
        patch_event=AsyncMock(return_value=None),
        delete_event=AsyncMock(return_value=None),
        list_changed_events=AsyncMock(return_value=([], None)),
    )
    monkeypatch.setattr(
        GoogleCalendarService, "_valid_access_token", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr(
        GoogleCalendarService, "_maybe_renew_channel", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(GoogleCalendarService, "_insert_event", mocks.insert_event)
    monkeypatch.setattr(GoogleCalendarService, "_patch_event", mocks.patch_event)
    monkeypatch.setattr(GoogleCalendarService, "_delete_event", mocks.delete_event)
    monkeypatch.setattr(
        GoogleCalendarService, "_list_changed_events", mocks.list_changed_events
    )
    return mocks


@pytest_asyncio.fixture
def calendar_service(
    session: AsyncSession, google_mocks: GoogleMocks
) -> GoogleCalendarService:
    return GoogleCalendarService(session)


@pytest_asyncio.fixture
def training_service(
    session: AsyncSession, calendar_service: GoogleCalendarService
) -> TrainingService:
    return TrainingService(session, calendar_service)


class TestPushSync:
    """create/update/delete/restore on TrainingService, with a
    calendar_connections row already present — see connection fixture."""

    async def test_creating_planned_session_creates_an_event(
        self,
        training_service: TrainingService,
        calendar_service: GoogleCalendarService,
        google_mocks: GoogleMocks,
        user: User,
        calendar_connection: CalendarConnection,
    ) -> None:
        workout = await training_service.create_session(
            user.id, WorkoutSessionCreate(session_date=TODAY)
        )

        google_mocks.insert_event.assert_awaited_once()
        mapping = await calendar_service.repo.get_mapping(workout.id)
        assert mapping is not None
        assert mapping.google_event_id

    async def test_creating_a_non_planned_session_does_not_sync(
        self,
        training_service: TrainingService,
        calendar_service: GoogleCalendarService,
        google_mocks: GoogleMocks,
        user: User,
        calendar_connection: CalendarConnection,
    ) -> None:
        workout = await training_service.create_session(
            user.id,
            WorkoutSessionCreate(session_date=TODAY, status=SessionStatus.SKIPPED),
        )

        google_mocks.insert_event.assert_not_awaited()
        assert await calendar_service.repo.get_mapping(workout.id) is None

    async def test_no_connection_is_a_no_op(
        self,
        training_service: TrainingService,
        google_mocks: GoogleMocks,
        user: User,
    ) -> None:
        """No calendar_connections row for this user — sync must not raise or
        call out to Google at all."""
        await training_service.create_session(
            user.id, WorkoutSessionCreate(session_date=TODAY)
        )

        google_mocks.insert_event.assert_not_awaited()

    async def test_leaving_planned_deletes_the_event(
        self,
        training_service: TrainingService,
        calendar_service: GoogleCalendarService,
        google_mocks: GoogleMocks,
        user: User,
        calendar_connection: CalendarConnection,
    ) -> None:
        workout = await training_service.create_session(
            user.id, WorkoutSessionCreate(session_date=TODAY)
        )

        await training_service.update_session(
            user.id, workout.id, WorkoutSessionUpdate(status=SessionStatus.COMPLETED)
        )

        google_mocks.delete_event.assert_awaited_once()
        assert await calendar_service.repo.get_mapping(workout.id) is None

    async def test_soft_delete_removes_the_event(
        self,
        training_service: TrainingService,
        calendar_service: GoogleCalendarService,
        google_mocks: GoogleMocks,
        user: User,
        calendar_connection: CalendarConnection,
    ) -> None:
        workout = await training_service.create_session(
            user.id, WorkoutSessionCreate(session_date=TODAY)
        )

        await training_service.delete_session(user.id, workout.id)

        google_mocks.delete_event.assert_awaited_once()
        assert await calendar_service.repo.get_mapping(workout.id) is None

    async def test_restore_recreates_the_event(
        self,
        training_service: TrainingService,
        calendar_service: GoogleCalendarService,
        google_mocks: GoogleMocks,
        user: User,
        calendar_connection: CalendarConnection,
    ) -> None:
        workout = await training_service.create_session(
            user.id, WorkoutSessionCreate(session_date=TODAY)
        )
        await training_service.delete_session(user.id, workout.id)

        await training_service.restore_session(user.id, workout.id)

        assert google_mocks.insert_event.await_count == 2
        assert await calendar_service.repo.get_mapping(workout.id) is not None


class TestPullChanges:
    async def test_unknown_channel_returns_no_changes(
        self, calendar_service: GoogleCalendarService
    ) -> None:
        assert await calendar_service.pull_changes(uuid4()) == []

    async def test_cancelled_event_maps_to_a_deletion(
        self,
        training_service: TrainingService,
        calendar_service: GoogleCalendarService,
        google_mocks: GoogleMocks,
        user: User,
        calendar_connection: CalendarConnection,
    ) -> None:
        workout = await training_service.create_session(
            user.id, WorkoutSessionCreate(session_date=TODAY)
        )
        mapping = await calendar_service.repo.get_mapping(workout.id)
        google_mocks.list_changed_events.return_value = (
            [{"id": mapping.google_event_id, "status": "cancelled"}],
            "next-token",
        )

        changes = await calendar_service.pull_changes(calendar_connection.channel_id)

        assert len(changes) == 1
        assert changes[0].deleted is True
        assert changes[0].session_id == workout.id
        assert changes[0].user_id == user.id

    async def test_moved_event_maps_to_a_reschedule(
        self,
        training_service: TrainingService,
        calendar_service: GoogleCalendarService,
        google_mocks: GoogleMocks,
        user: User,
        calendar_connection: CalendarConnection,
    ) -> None:
        workout = await training_service.create_session(
            user.id, WorkoutSessionCreate(session_date=TODAY)
        )
        mapping = await calendar_service.repo.get_mapping(workout.id)
        new_date = TODAY + timedelta(days=3)
        google_mocks.list_changed_events.return_value = (
            [{"id": mapping.google_event_id, "start": {"date": new_date.isoformat()}}],
            "next-token",
        )

        changes = await calendar_service.pull_changes(calendar_connection.channel_id)

        assert len(changes) == 1
        assert changes[0].deleted is False
        assert changes[0].new_date == new_date

    async def test_event_with_no_mapping_is_ignored(
        self,
        calendar_service: GoogleCalendarService,
        google_mocks: GoogleMocks,
        calendar_connection: CalendarConnection,
    ) -> None:
        """An event created directly in Google Calendar has no reliable
        mapping to exercises — out of scope, see the service's docstring."""
        google_mocks.list_changed_events.return_value = (
            [{"id": "not-ours", "start": {"date": TODAY.isoformat()}}],
            "next-token",
        )

        assert await calendar_service.pull_changes(calendar_connection.channel_id) == []


class TestWebhookEndpoint:
    async def test_rejects_a_bad_channel_token(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "GOOGLE_CALENDAR_WEBHOOK_TOKEN", "the-real-token")
        response = await client.post(
            BASE + "/webhook", headers={"X-Goog-Channel-Token": "wrong"}
        )

        assert response.status_code == 403

    async def test_sync_handshake_is_a_no_op(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "GOOGLE_CALENDAR_WEBHOOK_TOKEN", "the-real-token")
        response = await client.post(
            BASE + "/webhook",
            headers={
                "X-Goog-Channel-Token": "the-real-token",
                "X-Goog-Resource-State": "sync",
                "X-Goog-Channel-Id": str(uuid4()),
            },
        )

        assert response.status_code == 200


class TestConnectEndpoint:
    """`/connect` is auth'd via an `access_token` query param, not the
    `Authorization` header — it's meant to be opened directly in a browser
    (expo-web-browser on the mobile side), which can't attach headers to a
    plain navigation. See the endpoint's docstring."""

    async def _access_token(self, client: AsyncClient) -> str:
        await client.post(AUTH + "/register", json=CREDENTIALS)
        login = await client.post(
            AUTH + "/login",
            json={"email": CREDENTIALS["email"], "password": CREDENTIALS["password"]},
        )
        return login.json()["access_token"]

    async def test_requires_a_token(self, client: AsyncClient) -> None:
        response = await client.get(BASE + "/connect", follow_redirects=False)
        assert response.status_code == 422

    async def test_rejects_a_garbage_token(self, client: AsyncClient) -> None:
        response = await client.get(
            BASE + "/connect",
            params={"access_token": "not-a-real-token"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    async def test_requires_calendar_configuration(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-client-secret")
        monkeypatch.setattr(settings, "GOOGLE_CALENDAR_WEBHOOK_URL", "")
        token = await self._access_token(client)

        response = await client.get(
            BASE + "/connect",
            params={"access_token": token},
            follow_redirects=False,
        )

        assert response.status_code == 503

    async def test_redirects_with_calendar_scope_and_offline_access(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-client-secret")
        monkeypatch.setattr(
            settings, "GOOGLE_CALENDAR_WEBHOOK_URL", "https://api.example.com/webhook"
        )
        monkeypatch.setattr(settings, "GOOGLE_CALENDAR_WEBHOOK_TOKEN", "webhook-secret")
        token = await self._access_token(client)

        response = await client.get(
            BASE + "/connect",
            params={"access_token": token},
            follow_redirects=False,
        )

        assert response.status_code == 307
        query = parse_qs(urlparse(response.headers["location"]).query)
        assert "https://www.googleapis.com/auth/calendar.events" in query["scope"][0]
        assert query["access_type"] == ["offline"]
        assert query["prompt"] == ["consent"]
