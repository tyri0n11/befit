"""Process-wide Redis client.

Mirrors app/core/database.py: one client, therefore one connection pool, per
process. Used for OAuth state, which needs single-use semantics that a signed
stateless value cannot provide — the state must be *consumed*, not just verified,
or a captured callback URL could be replayed.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis, from_url

from app.core.settings import settings


class RedisCache:
    _instance: "RedisCache | None" = None

    _client: Redis | None

    def __new__(cls) -> "RedisCache":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._client = None
        return cls._instance

    @property
    def client(self) -> Redis:
        if self._client is None:
            raise RuntimeError("RedisCache.connect() has not been called")
        return self._client

    def connect(self) -> None:
        """Build the client. Idempotent; opens no socket until first use."""
        if self._client is not None:
            return
        self._client = from_url(settings.REDIS_URL, decode_responses=True)

    async def disconnect(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None


cache = RedisCache()


@asynccontextmanager
async def redis_lifespan() -> AsyncIterator[None]:
    cache.connect()
    try:
        yield
    finally:
        await cache.disconnect()


def get_redis() -> Redis:
    """FastAPI dependency."""
    return cache.client
