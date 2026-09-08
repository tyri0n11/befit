"""Data access for Google Calendar sync. Every lookup is scoped by `user_id`,
same convention as app/repositories/training.py."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import CalendarConnection, SessionCalendarEvent


class CalendarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- connections --------------------------------------------------

    async def get_connection(self, user_id: int) -> CalendarConnection | None:
        result = await self.session.execute(
            select(CalendarConnection).where(CalendarConnection.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_connection_by_channel(
        self, channel_id: UUID
    ) -> CalendarConnection | None:
        result = await self.session.execute(
            select(CalendarConnection).where(
                CalendarConnection.channel_id == channel_id
            )
        )
        return result.scalar_one_or_none()

    def add_connection(self, connection: CalendarConnection) -> None:
        self.session.add(connection)

    async def delete_connection(self, connection: CalendarConnection) -> None:
        await self.session.delete(connection)
        await self.session.flush()

    # --- session <-> event mapping -------------------------------------

    async def get_mapping(self, session_id: int) -> SessionCalendarEvent | None:
        result = await self.session.execute(
            select(SessionCalendarEvent).where(
                SessionCalendarEvent.session_id == session_id
            )
        )
        return result.scalar_one_or_none()

    async def get_mapping_by_event(
        self, user_id: int, google_event_id: str
    ) -> SessionCalendarEvent | None:
        result = await self.session.execute(
            select(SessionCalendarEvent).where(
                SessionCalendarEvent.user_id == user_id,
                SessionCalendarEvent.google_event_id == google_event_id,
            )
        )
        return result.scalar_one_or_none()

    def add_mapping(self, mapping: SessionCalendarEvent) -> None:
        self.session.add(mapping)

    async def delete_mapping(self, mapping: SessionCalendarEvent) -> None:
        await self.session.delete(mapping)
        await self.session.flush()

    async def flush(self) -> None:
        await self.session.flush()
