import enum
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BaseModel, pg_enum


class MovementPattern(enum.StrEnum):
    SQUAT = "squat"
    HINGE = "hinge"
    LUNGE = "lunge"
    HORIZONTAL_PUSH = "horizontal_push"
    VERTICAL_PUSH = "vertical_push"
    HORIZONTAL_PULL = "horizontal_pull"
    VERTICAL_PULL = "vertical_pull"
    ISOLATION = "isolation"
    CARRY = "carry"
    CORE = "core"


class EquipmentType(enum.StrEnum):
    BARBELL = "barbell"
    DUMBBELL = "dumbbell"
    MACHINE = "machine"
    CABLE = "cable"
    BODYWEIGHT = "bodyweight"
    KETTLEBELL = "kettlebell"
    BAND = "band"
    SMITH_MACHINE = "smith_machine"


class ForceType(enum.StrEnum):
    PUSH = "push"
    PULL = "pull"
    STATIC = "static"


class MuscleRole(enum.StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    STABILIZER = "stabilizer"


class MuscleGroup(Base):
    """Self-referencing tree, max depth 2. Only leaves (`is_trackable`) may be
    mapped to an exercise — `trg_exercise_muscles_leaf_only` enforces it."""

    __tablename__ = "muscle_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name_en: Mapped[str] = mapped_column(String(64))
    name_vi: Mapped[str] = mapped_column(String(64))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("muscle_groups.id", ondelete="RESTRICT"), default=None, index=True
    )
    depth: Mapped[int] = mapped_column(SmallInteger, default=0)
    is_trackable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # No `parent`/`children` relationships on purpose: the tree is at most three
    # levels, so the service assembles it from one flat query. A self-referencing
    # lazy relationship would emit a load per node — and blow up under asyncio.


class Exercise(BaseModel):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name_en: Mapped[str] = mapped_column(String(100))
    name_vi: Mapped[str | None] = mapped_column(String(100), default=None)
    pattern: Mapped[MovementPattern] = mapped_column(
        pg_enum(MovementPattern, "movement_pattern")
    )
    equipment: Mapped[EquipmentType] = mapped_column(
        pg_enum(EquipmentType, "equipment_type")
    )
    force: Mapped[ForceType] = mapped_column(pg_enum(ForceType, "force_type"))
    is_unilateral: Mapped[bool] = mapped_column(Boolean, default=False)
    default_rest_sec: Mapped[int] = mapped_column(SmallInteger, default=90)
    requires_overhead: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    muscles: Mapped[list["ExerciseMuscle"]] = relationship(
        back_populates="exercise", cascade="all, delete-orphan"
    )


class ExerciseMuscle(Base):
    __tablename__ = "exercise_muscles"

    exercise_id: Mapped[int] = mapped_column(
        ForeignKey("exercises.id", ondelete="CASCADE"), primary_key=True
    )
    muscle_group_id: Mapped[int] = mapped_column(
        ForeignKey("muscle_groups.id", ondelete="RESTRICT"), primary_key=True
    )
    role: Mapped[MuscleRole] = mapped_column(pg_enum(MuscleRole, "muscle_role"))

    exercise: Mapped[Exercise] = relationship(back_populates="muscles")
    muscle_group: Mapped[MuscleGroup] = relationship(lazy="joined")

    # Flattened for the API layer: a link is only ever read together with the
    # muscle it points at, so responses carry the code instead of a bare id.
    @property
    def code(self) -> str:
        return self.muscle_group.code

    @property
    def name_en(self) -> str:
        return self.muscle_group.name_en

    @property
    def name_vi(self) -> str:
        return self.muscle_group.name_vi
