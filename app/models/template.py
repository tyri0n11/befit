from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BaseModel
from app.models.catalog import Exercise


class WorkoutTemplate(BaseModel):
    """A reusable plan owned by one user. Unlike the catalog, this is not shared
    master data — nothing seeds it."""

    __tablename__ = "workout_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    program_day: Mapped[str | None] = mapped_column(String(64), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    # Eager for the same reason as WorkoutSession.exercises: a template is never
    # read without its slots, and a lazy load would raise under asyncio.
    exercises: Mapped[list["TemplateExercise"]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="TemplateExercise.order_index",
        lazy="selectin",
    )

    @property
    def exercise_count(self) -> int:
        return len(self.exercises)


class TemplateExercise(Base):
    """Mirrors `SessionExercise` minus everything that only exists once a
    session has happened — status, skip_reason, and the sets."""

    __tablename__ = "template_exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("workout_templates.id", ondelete="CASCADE")
    )
    exercise_id: Mapped[int] = mapped_column(
        ForeignKey("exercises.id", ondelete="RESTRICT")
    )
    order_index: Mapped[int] = mapped_column(SmallInteger)
    target_sets: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    target_reps_min: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    target_reps_max: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    target_weight_kg: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2), default=None
    )
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    template: Mapped[WorkoutTemplate] = relationship(back_populates="exercises")
    exercise: Mapped[Exercise] = relationship(lazy="joined")

    # Flattened for the API layer — a slot is never rendered without naming the
    # exercise it points at.
    @property
    def slug(self) -> str:
        return self.exercise.slug

    @property
    def name_en(self) -> str:
        return self.exercise.name_en
