"""Test fixtures.

Every test runs inside a transaction that is rolled back afterwards, so the
suite never leaves rows behind. This works by overriding the `get_db`
dependency with a session bound to an outer transaction we control — the
service layer only ever flushes, and the commit that `db.session()` would
normally issue is bypassed.

The suite needs a running database: `make up` (postgres on the host port from
`.env`, 5433 by default).
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.core.database import db, get_db
from app.core.redis import get_redis
from app.core.settings import settings
from app.main import app
from app.services.email import get_email_sender


@pytest_asyncio.fixture(scope="session")
async def connection() -> AsyncIterator[AsyncConnection]:
    db.connect(echo=False)
    async with db.engine.connect() as conn:
        yield conn
    await db.disconnect()


@pytest_asyncio.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    transaction = await connection.begin()
    factory = async_sessionmaker(
        bind=connection, expire_on_commit=False, autoflush=False
    )
    async with factory() as s:
        yield s
    # Discards everything the test wrote, including rows the service flushed.
    await transaction.rollback()


class RecordingEmailSender:
    """Captures outgoing mail so tests can read the raw token. Nothing leaves
    the process — the suite must never hit the Resend API."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_password_reset(self, to: str, token: str) -> None:
        self.sent.append((to, token))


@pytest_asyncio.fixture
def mailer() -> RecordingEmailSender:
    return RecordingEmailSender()


class FakeRedis:
    """In-memory stand-in for the OAuth state store.

    Only get/set/delete are used, so a dict is enough and avoids both a new
    dependency and a Redis server in the test path. TTLs are irrelevant here —
    what the tests care about is that state is *deleted* when consumed.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest_asyncio.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest_asyncio.fixture
def google_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend Google credentials are present. Nothing contacts Google: the tests
    only exercise the redirect we build and the state we store."""
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-client-secret")


@pytest_asyncio.fixture
def google_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the credentials empty. Not the same as leaving them alone: a
    developer with real values in `.env` would otherwise see the endpoint
    redirect and the "not configured" test fail for no fault of the code."""
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "")


@pytest_asyncio.fixture
async def client(
    session: AsyncSession,
    mailer: RecordingEmailSender,
    fake_redis: FakeRedis,
) -> AsyncIterator[AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_email_sender] = lambda: mailer
    app.dependency_overrides[get_redis] = lambda: fake_redis
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
