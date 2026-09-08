from datetime import datetime

from pydantic import BaseModel


class CalendarStatusResponse(BaseModel):
    connected: bool
    calendar_id: str | None = None
    connected_at: datetime | None = None
