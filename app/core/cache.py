"""Read-through JSON cache over Redis.

Only Pydantic values go in, so what is stored is the exact response body the API
would have produced — no ORM objects, no pickle. A cache miss and a broken Redis
are the same thing to the caller: both fall through to the loader, because a
cache outage must not turn into a 500.
"""

import logging
from collections.abc import Awaitable, Callable

from pydantic import TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class JsonCache:
    def __init__(self, redis: Redis, *, prefix: str, ttl: int) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl = ttl

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    async def get_or_set[T](
        self,
        key: str,
        adapter: TypeAdapter[T],
        loader: Callable[[], Awaitable[T]],
    ) -> T:
        if not self.enabled:
            return await loader()

        full_key = self._key(key)
        try:
            raw = await self.redis.get(full_key)
        except RedisError:
            logger.warning("cache read failed for %s", full_key, exc_info=True)
            return await loader()

        if raw is not None:
            try:
                return adapter.validate_json(raw)
            except ValidationError:
                # The schema changed under a still-live key. Reload rather than
                # fail, and let the write below overwrite the stale shape.
                logger.info("discarding stale cache entry %s", full_key)

        value = await loader()
        try:
            await self.redis.set(full_key, adapter.dump_json(value), ex=self.ttl)
        except RedisError:
            logger.warning("cache write failed for %s", full_key, exc_info=True)
        return value

    async def invalidate(self) -> int:
        """Drop every key under this prefix. Called after a seed run — the whole
        namespace goes at once because master data is rewritten wholesale."""
        removed = 0
        try:
            async for key in self.redis.scan_iter(match=f"{self.prefix}:*"):
                removed += await self.redis.delete(key)
        except RedisError:
            logger.warning("cache invalidation failed for %s", self.prefix)
        return removed
