from pydantic import BaseModel, ConfigDict, Field

from app.models.catalog import (
    EquipmentType,
    ForceType,
    LoadType,
    MovementPattern,
    MuscleRole,
)


class MuscleGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name_en: str
    name_vi: str
    parent_id: int | None
    depth: int
    is_trackable: bool


class MuscleGroupNode(MuscleGroupResponse):
    """A muscle group with its subtree. Regions are depth 0, leaves depth 2."""

    children: list["MuscleGroupNode"] = Field(default_factory=list)


class ExerciseMuscleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: MuscleRole
    code: str
    name_en: str
    name_vi: str


class ExerciseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name_en: str
    name_vi: str | None
    pattern: MovementPattern
    equipment: EquipmentType
    force: ForceType
    is_unilateral: bool
    load_type: LoadType
    default_rest_sec: int
    requires_overhead: bool
    notes: str | None
    muscles: list[ExerciseMuscleResponse]


class ExercisePage(BaseModel):
    items: list[ExerciseResponse]
    total: int
    limit: int
    offset: int
