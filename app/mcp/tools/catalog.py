"""Catalog tools — read-only master data, no auth, mirrors
app/api/v1/endpoints/catalog.py.
"""

from app.mcp.context import catalog_session, redis_client
from app.mcp.server import mcp
from app.models.catalog import EquipmentType, ForceType, MovementPattern, MuscleRole
from app.repositories.catalog import ExerciseFilters
from app.schemas.catalog import (
    ExercisePage,
    ExerciseResponse,
    MuscleGroupNode,
    MuscleGroupResponse,
)
from app.services.catalog import CatalogService, catalog_cache


@mcp.tool()
async def list_exercises(
    q: str | None = None,
    pattern: MovementPattern | None = None,
    equipment: EquipmentType | None = None,
    force: ForceType | None = None,
    muscle: str | None = None,
    role: MuscleRole | None = None,
    is_unilateral: bool | None = None,
    requires_overhead: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> ExercisePage:
    """Browse the exercise catalog. `muscle` is a muscle group code, e.g. `lats`."""
    filters = ExerciseFilters(
        search=q,
        pattern=pattern,
        equipment=equipment,
        force=force,
        muscle=muscle,
        role=role,
        is_unilateral=is_unilateral,
        requires_overhead=requires_overhead,
    )
    async with catalog_session() as session:
        service = CatalogService(session, catalog_cache(redis_client()))
        return await service.list_exercises(filters, limit=limit, offset=offset)


@mcp.tool()
async def get_exercise(slug: str) -> ExerciseResponse | None:
    """A single exercise by slug, or null if no exercise has that slug."""
    async with catalog_session() as session:
        service = CatalogService(session, catalog_cache(redis_client()))
        return await service.get_exercise(slug)


@mcp.tool()
async def list_muscle_groups(trackable_only: bool = False) -> list[MuscleGroupResponse]:
    """Muscle groups as a flat list. `trackable_only` returns just the leaves —
    the nodes an exercise can map to."""
    async with catalog_session() as session:
        service = CatalogService(session, catalog_cache(redis_client()))
        return await service.list_muscle_groups(trackable_only=trackable_only)


@mcp.tool()
async def muscle_group_tree() -> list[MuscleGroupNode]:
    """Muscle groups nested region -> group -> leaf."""
    async with catalog_session() as session:
        service = CatalogService(session, catalog_cache(redis_client()))
        return await service.muscle_group_tree()
