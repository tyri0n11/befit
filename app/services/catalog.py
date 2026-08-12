"""Read-only access to catalog master data.

Master data is written by `scripts/seed.py` from `scripts/data/*.yaml`, so this
service exposes queries only — there is no write path here on purpose.

That same property is what makes the Redis read-through cache safe: nothing but
a seed run can change the answers, so entries only need to expire (or be dropped
by `scripts/seed.py`), never to be invalidated per write. The service therefore
returns Pydantic responses rather than ORM objects — what is cached is exactly
what the endpoint serialises.
"""

import hashlib
from dataclasses import astuple

from pydantic import TypeAdapter
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import JsonCache
from app.core.settings import settings
from app.repositories.catalog import CatalogRepository, ExerciseFilters
from app.schemas.catalog import (
    ExercisePage,
    ExerciseResponse,
    MuscleGroupNode,
    MuscleGroupResponse,
)

CATALOG_CACHE_PREFIX = "catalog:v1"

_PAGE = TypeAdapter(ExercisePage)
_EXERCISE = TypeAdapter(ExerciseResponse | None)
_GROUPS = TypeAdapter(list[MuscleGroupResponse])
_TREE = TypeAdapter(list[MuscleGroupNode])


def catalog_cache(redis: Redis) -> JsonCache:
    return JsonCache(
        redis,
        prefix=CATALOG_CACHE_PREFIX,
        ttl=settings.CATALOG_CACHE_TTL_SECONDS,
    )


class CatalogService:
    def __init__(self, session: AsyncSession, cache: JsonCache) -> None:
        self.repo = CatalogRepository(session)
        self.cache = cache

    async def list_exercises(
        self, filters: ExerciseFilters, *, limit: int, offset: int
    ) -> ExercisePage:
        async def load() -> ExercisePage:
            total = await self.repo.count_exercises(filters)
            items = await self.repo.list_exercises(filters, limit=limit, offset=offset)
            return ExercisePage(
                items=[ExerciseResponse.model_validate(e) for e in items],
                total=total,
                limit=limit,
                offset=offset,
            )

        return await self.cache.get_or_set(
            _filter_key(filters, limit=limit, offset=offset), _PAGE, load
        )

    async def get_exercise(self, slug: str) -> ExerciseResponse | None:
        async def load() -> ExerciseResponse | None:
            exercise = await self.repo.get_exercise_by_slug(slug)
            if exercise is None:
                return None
            return ExerciseResponse.model_validate(exercise)

        # A miss is cached too: unknown slugs are the shape a scraper produces,
        # and re-querying for each one is exactly what the cache is here to stop.
        return await self.cache.get_or_set(f"ex:{slug}", _EXERCISE, load)

    async def list_muscle_groups(
        self, *, trackable_only: bool = False
    ) -> list[MuscleGroupResponse]:
        async def load() -> list[MuscleGroupResponse]:
            rows = await self.repo.list_muscle_groups(trackable_only=trackable_only)
            return [MuscleGroupResponse.model_validate(r) for r in rows]

        return await self.cache.get_or_set(
            f"mg:flat:{int(trackable_only)}", _GROUPS, load
        )

    async def muscle_group_tree(self) -> list[MuscleGroupNode]:
        return await self.cache.get_or_set("mg:tree", _TREE, self._build_tree)

    async def _build_tree(self) -> list[MuscleGroupNode]:
        """The full tree, roots first. Built in Python from one flat query —
        the tree is at most three levels and a few dozen rows."""
        rows = await self.repo.list_muscle_groups()
        nodes = {row.id: MuscleGroupNode.model_validate(row) for row in rows}
        roots: list[MuscleGroupNode] = []
        # `rows` is ordered by depth, so a parent is always in `nodes` already.
        for row in rows:
            node = nodes[row.id]
            if row.parent_id is None:
                roots.append(node)
            else:
                nodes[row.parent_id].children.append(node)
        return roots


def _filter_key(filters: ExerciseFilters, *, limit: int, offset: int) -> str:
    """Every field participates, so two different filter sets can never collide
    on one entry. The dataclass is frozen with a fixed field order, which is what
    makes `astuple` stable; `repr` keeps a search term containing the separator
    from reading as two fields."""
    parts = "|".join(repr(v) for v in astuple(filters))
    digest = hashlib.sha1(parts.encode(), usedforsecurity=False).hexdigest()[:16]
    return f"ex:list:{limit}:{offset}:{digest}"
