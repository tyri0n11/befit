"""Bearer-token auth and session/redis access for MCP tools.

Mirrors app/api/v1/dependencies.py, but for tool calls instead of FastAPI
`Depends`: there is no request-scoped DI here, so each tool opens its own
`db.session()` — same commit-on-success/rollback-on-exception scope `get_db`
gives every REST endpoint.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import db
from app.core.redis import cache
from app.models.user import User
from app.services.auth import AuthError, AuthService
from app.services.email import get_email_sender
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

# Module-level reference (not a call) so tests can monkeypatch it the same way
# tests/conftest.py overrides get_db, keeping MCP tests inside the project's
# rolled-back-transaction convention.
db_session = db.session


def redis_client() -> Redis:
    return cache.client


def _bearer_token(ctx: Context) -> str:
    headers = ctx.headers or {}
    auth_header = headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ToolError("Not authenticated: missing or malformed Authorization header")
    return token


async def _user_from_token(session: AsyncSession, token: str) -> User:
    service = AuthService(session, get_email_sender())
    try:
        return await service.user_from_access_token(token)
    except AuthError as exc:
        raise ToolError(f"{exc.code.value}: {exc.message}") from exc


@asynccontextmanager
async def authenticated_session(
    ctx: Context,
) -> AsyncIterator[tuple[AsyncSession, User]]:
    """One `db.session()` serving both the auth lookup and the tool's own work."""
    token = _bearer_token(ctx)
    async with db_session() as session:
        user = await _user_from_token(session, token)
        yield session, user


@asynccontextmanager
async def catalog_session() -> AsyncIterator[AsyncSession]:
    """No auth — the catalog is public reference data, same as the REST routes."""
    async with db_session() as session:
        yield session
