"""Tests for the MCP tool layer (app/mcp/).

Two tests deliberately deviate from the project's every-test-rolls-back
convention (see module docstring of tests/conftest.py): the MCP tools open
their own `db.session()` per call, the same way a REST request does through
`get_db`, so they never see the transaction `app.dependency_overrides[get_db]`
binds tests to. `test_mcp_end_to_end_authenticated_round_trip` therefore talks
to the real, committed database and cleans up everything it wrote itself.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest
import pytest_asyncio
from httpx import AsyncClient
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.database import db
from app.core.redis import cache
from app.core.settings import settings
from app.main import app
from app.mcp import context as mcp_context
from app.mcp.server import mcp as mcp_server
from app.models.user import User

AUTH = f"{settings.API_V1_STR}/auth"

EXPECTED_TOOLS = {
    "list_exercises",
    "get_exercise",
    "list_muscle_groups",
    "muscle_group_tree",
    "list_sessions",
    "create_session",
    "get_session",
    "update_session",
    "delete_session",
    "restore_session",
    "purge_session",
    "add_session_exercise",
    "update_session_exercise",
    "delete_session_exercise",
    "log_set",
    "update_set",
    "delete_set",
    "list_templates",
    "create_template",
    "template_from_session",
    "get_template",
    "update_template",
    "delete_template",
    "instantiate_template",
    "add_template_exercise",
    "update_template_exercise",
    "delete_template_exercise",
    "volume",
    "by_muscle",
    "by_exercise",
}


class FakeContext:
    """Duck-types the one thing app/mcp/context.py reads off a real Context —
    `.headers` — without needing a live MCP request/session."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self._headers = headers or {}

    @property
    def headers(self) -> dict[str, str]:
        return self._headers


@pytest_asyncio.fixture
def patch_mcp_db(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
    """Routes app.mcp.context.db_session to the test's rolled-back transaction,
    the same way tests/conftest.py's `client` fixture overrides get_db."""

    @asynccontextmanager
    async def fake_db_session() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(mcp_context, "db_session", fake_db_session)


async def test_all_tools_registered() -> None:
    tools = await mcp_server.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS


async def test_authenticated_session_valid_token(
    client: AsyncClient, patch_mcp_db: None
) -> None:
    creds = {
        "email": "mcp-unit@example.com",
        "display_name": "MCP Unit",
        "password": "correct-horse-battery",
    }
    assert (await client.post(AUTH + "/register", json=creds)).status_code == 201
    login = await client.post(
        AUTH + "/login", json={"email": creds["email"], "password": creds["password"]}
    )
    token = login.json()["access_token"]

    ctx = FakeContext({"authorization": f"Bearer {token}"})
    async with mcp_context.authenticated_session(ctx) as (_session, user):
        assert user.email == creds["email"]


async def test_authenticated_session_missing_header(patch_mcp_db: None) -> None:
    with pytest.raises(ToolError, match="Not authenticated"):
        async with mcp_context.authenticated_session(FakeContext()):
            pass


async def test_authenticated_session_malformed_scheme(patch_mcp_db: None) -> None:
    ctx = FakeContext({"authorization": "Token abc123"})
    with pytest.raises(ToolError, match="Not authenticated"):
        async with mcp_context.authenticated_session(ctx):
            pass


async def test_authenticated_session_invalid_token(patch_mcp_db: None) -> None:
    ctx = FakeContext({"authorization": "Bearer not-a-real-token"})
    with pytest.raises(ToolError, match="INVALID_TOKEN"):
        async with mcp_context.authenticated_session(ctx):
            pass


async def test_mcp_end_to_end_authenticated_round_trip(
    connection: AsyncConnection,  # ensures db.connect() has already run
) -> None:
    """Drives the real streamable-HTTP mount end to end: register, login, one
    no-auth catalog call, then an authenticated write + read. Everything here
    commits for real (see module docstring), so the test cleans up the session
    and the user it created itself."""
    cache.connect()  # idempotent — the catalog tool needs a real Redis client
    email = f"mcp-e2e-{uuid.uuid4().hex}@example.com"
    password = "correct-horse-battery"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://mcp-test"
    ) as rest:
        register = await rest.post(
            AUTH + "/register",
            json={"email": email, "password": password, "display_name": "MCP E2E"},
        )
        assert register.status_code == 201, register.text
        login = await rest.post(
            AUTH + "/login", json={"email": email, "password": password}
        )
        token = login.json()["access_token"]

    try:
        async with mcp_server.session_manager.run():
            http_client = httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://mcp-test",
                headers={"Authorization": f"Bearer {token}"},
            )
            async with (
                streamable_http_client(
                    "http://mcp-test/mcp/", http_client=http_client
                ) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as mcp_client,
            ):
                await mcp_client.initialize()

                groups = await mcp_client.call_tool(
                    "list_muscle_groups", {"trackable_only": True}
                )
                assert groups.is_error is False

                created = await mcp_client.call_tool(
                    "create_session", {"payload": {"session_date": "2026-01-01"}}
                )
                assert created.is_error is False
                session_id = created.structured_content["id"]
                assert created.structured_content["status"] == "planned"

                fetched = await mcp_client.call_tool(
                    "get_session", {"session_id": session_id}
                )
                assert fetched.structured_content["id"] == session_id

                await mcp_client.call_tool("delete_session", {"session_id": session_id})
                await mcp_client.call_tool("purge_session", {"session_id": session_id})
    finally:
        async with db.session() as cleanup_session:
            await cleanup_session.execute(delete(User).where(User.email == email))
