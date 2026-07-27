import enum
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BaseModel, pg_enum


class UserStatus(enum.StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class AuthProvider(enum.StrEnum):
    GOOGLE = "google"
    LOCAL = "local"


class User(BaseModel):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status"), default=UserStatus.ACTIVE
    )
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Bumped on credential changes; issued tokens carry the value they were
    # signed with, so raising it revokes every outstanding token for this user.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    auth_methods: Mapped[list["UserAuth"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class UserAuth(Base):
    """One row per login method. `ck_auth_shape` enforces that a local row
    carries a password hash and no provider id, and vice versa for google."""

    __tablename__ = "user_auth"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[AuthProvider] = mapped_column(
        pg_enum(AuthProvider, "auth_provider")
    )
    provider_user_id: Mapped[str | None] = mapped_column(String(255), default=None)
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="auth_methods")


class PasswordResetToken(Base):
    """Single-use reset token. Only the SHA-256 digest is stored — see the note
    in scripts/database/01_init_user.sql."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped[User] = relationship()
