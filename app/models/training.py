import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BaseModel, pg_enum
from app.models.catalog import Exercise


class SessionStatus(enum.StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class ExerciseStatus(enum.StrEnum):
    PLANNED = "planned"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class Bucket(enum.StrEnum):
    """Granularity of a volume series. Not a database type — it lives here
    because the repository, the service and the schemas all need it, and models
    is the layer they may all import."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class WorkoutSession(BaseModel):
    """One training day for one user."""

    __tablename__ = "workout_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    session_date: Mapped[date]
    status: Mapped[SessionStatus] = mapped_column(
        pg_enum(SessionStatus, "session_status"), default=SessionStatus.PLANNED
    )
    bodyweight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    program_day: Mapped[str | None] = mapped_column(String(64), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    # Soft delete — NULL is live. Reads filter on it instead of removing the
    # row, so a mis-tap never destroys logged training.
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    # The whole graph is eager: a session is never read without its exercises,
    # and a lazy load would raise under asyncio.
    exercises: Mapped[list["SessionExercise"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionExercise.order_index",
        lazy="selectin",
    )

    @property
    def tonnage(self) -> float:
        return float(sum(e.tonnage for e in self.exercises))

    @property
    def exercise_count(self) -> int:
        return len(self.exercises)

    @property
    def set_count(self) -> int:
        return sum(len(e.sets) for e in self.exercises)


class SessionExercise(Base):
    """One exercise slot within a session, with its planned targets."""

    __tablename__ = "session_exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("workout_sessions.id", ondelete="CASCADE")
    )
    exercise_id: Mapped[int] = mapped_column(
        ForeignKey("exercises.id", ondelete="RESTRICT")
    )
    order_index: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[ExerciseStatus] = mapped_column(
        pg_enum(ExerciseStatus, "exercise_status"), default=ExerciseStatus.PLANNED
    )
    target_sets: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    target_reps_min: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    target_reps_max: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    target_weight_kg: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2), default=None
    )
    skip_reason: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    session: Mapped[WorkoutSession] = relationship(back_populates="exercises")
    exercise: Mapped[Exercise] = relationship(lazy="joined")
    sets: Mapped[list["SetLog"]] = relationship(
        back_populates="session_exercise",
        cascade="all, delete-orphan",
        order_by="SetLog.set_index",
        lazy="selectin",
    )

    # Flattened for the API layer — a slot is never rendered without naming the
    # exercise it points at.
    @property
    def slug(self) -> str:
        return self.exercise.slug

    @property
    def name_en(self) -> str:
        return self.exercise.name_en

    @property
    def tonnage(self) -> float:
        """Never stored — see the header of 03_init_training.sql. A unilateral
        movement counts double because the logged weight is per side."""
        factor = 2 if self.exercise.is_unilateral else 1
        return float(
            sum(s.weight_kg * factor * s.reps for s in self.sets if s.weight_kg)
        )


class SetLog(Base):
    """One performed set. `logged_at` is the only timestamp — a set is written
    once and rarely amended."""

    __tablename__ = "set_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_exercise_id: Mapped[int] = mapped_column(
        ForeignKey("session_exercises.id", ondelete="CASCADE")
    )
    set_index: Mapped[int] = mapped_column(SmallInteger)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), default=None)
    reps: Mapped[int] = mapped_column(SmallInteger)
    rpe: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), default=None)
    rest_before_sec: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    rom_note: Mapped[str | None] = mapped_column(Text, default=None)
    logged_at: Mapped[datetime] = mapped_column(server_default=func.now())

    session_exercise: Mapped[SessionExercise] = relationship(back_populates="sets")
