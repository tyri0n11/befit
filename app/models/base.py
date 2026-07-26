from datetime import datetime
from typing import ClassVar

from sqlalchemy import DateTime, func
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
