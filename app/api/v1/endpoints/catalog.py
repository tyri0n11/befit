from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.dependencies import get_catalog_service
from app.models.catalog import (
    EquipmentType,
    ForceType,
    MovementPattern,
    MuscleRole,
)
from app.repositories.catalog import ExerciseFilters
from app.schemas.catalog import (
    ExercisePage,
    ExerciseResponse,
    MuscleGroupNode,
    MuscleGroupResponse,
)
from app.services.catalog import CatalogService

router = APIRouter(tags=["catalog"])


@router.get(
    "/exercises",
    response_model=ExercisePage,
    summary="Browse the exercise catalog",
)
async def list_exercises(
    q: str | None = Query(None, max_length=100, description="Match name or slug"),
    pattern: MovementPattern | None = None,
    equipment: EquipmentType | None = None,
    force: ForceType | None = None,
    muscle: str | None = Query(
        None, max_length=32, description="Muscle group code, e.g. `lats`"
    ),
    role: MuscleRole | None = Query(
        None, description="Narrows `muscle` to that role; alone, any exercise with it"
    ),
    is_unilateral: bool | None = None,
    requires_overhead: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: CatalogService = Depends(get_catalog_service),
) -> ExercisePage:
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
    return await service.list_exercises(filters, limit=limit, offset=offset)


@router.get(
    "/exercises/{slug}",
    response_model=ExerciseResponse,
    summary="A single exercise by slug",
)
async def get_exercise(
    slug: str,
    service: CatalogService = Depends(get_catalog_service),
) -> ExerciseResponse:
    exercise = await service.get_exercise(slug)
    if exercise is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No exercise with slug '{slug}'",
        )
    return exercise


@router.get(
    "/muscle-groups",
    response_model=list[MuscleGroupResponse],
    summary="Muscle groups as a flat list",
)
async def list_muscle_groups(
    trackable_only: bool = Query(
        False, description="Only leaves — the nodes an exercise can map to"
    ),
    service: CatalogService = Depends(get_catalog_service),
) -> list[MuscleGroupResponse]:
    return await service.list_muscle_groups(trackable_only=trackable_only)


@router.get(
    "/muscle-groups/tree",
    response_model=list[MuscleGroupNode],
    summary="Muscle groups nested region → group → leaf",
)
async def muscle_group_tree(
    service: CatalogService = Depends(get_catalog_service),
) -> list[MuscleGroupNode]:
    return await service.muscle_group_tree()
