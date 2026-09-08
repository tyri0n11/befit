"""Two-way sync between planned workout sessions and one Google Calendar per
user. See scripts/database/05_init_calendar.sql and
app/services/google_calendar.py for the mechanics."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class CalendarConnection(BaseModel):
    """One row per user — a user connects exactly one Google Calendar."""

    __tablename__ = "calendar_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    token_expires_at: Mapped[datetime]
    scope: Mapped[str] = mapped_column(Text)
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary")
    # Push notification channel for the pull direction — unset until the
    # first watch channel is registered.
    channel_id: Mapped[UUID | None] = mapped_column(default=None)
    resource_id: Mapped[str | None] = mapped_column(String(255), default=None)
    channel_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    # events.list incremental sync cursor; None forces a full sync next pull.
    sync_token: Mapped[str | None] = mapped_column(Text, default=None)


class SessionCalendarEvent(BaseModel):
    """Maps a workout session to the Google event that represents it."""

    __tablename__ = "session_calendar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("workout_sessions.id", ondelete="CASCADE"), unique=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    google_event_id: Mapped[str] = mapped_column(String(255))
