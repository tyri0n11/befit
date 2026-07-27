import enum
from datetime import datetime
from typing import ClassVar

from sqlalchemy import DateTime, Enum, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeEngine


class Base(DeclarativeBase):
    # The SQL scripts use TIMESTAMPTZ everywhere, but SQLAlchemy maps a bare
    # `datetime` annotation to TIMESTAMP WITHOUT TIME ZONE. Without this, passing
    # an aware datetime raises "can't subtract offset-naive and offset-aware
    # datetimes" from asyncpg. Declared here so every model inherits it.
    type_annotation_map: ClassVar[dict[object, TypeEngine]] = {
        datetime: DateTime(timezone=True)
    }


class BaseModel(Base):
    __abstract__ = True

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


# The enum types are created by scripts/database/*.sql, so SQLAlchemy must not
# try to emit CREATE TYPE. `values_callable` stores the label ('active') rather
# than the member name ('ACTIVE').
def pg_enum(python_enum: type[enum.Enum], name: str) -> Enum:
    return Enum(
        python_enum,
        name=name,
        create_type=False,
        native_enum=True,
        values_callable=lambda e: [member.value for member in e],
    )
