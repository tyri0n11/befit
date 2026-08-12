from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class OAuthClient(BaseModel):
    """A registered MCP client (RFC 7591 Dynamic Client Registration).

    The one model in this codebase whose primary key isn't a `SERIAL` int —
    the SDK, not us, mints `client_id` as a UUID string on registration.
    """

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(Text, primary_key=True)
    client_secret: Mapped[str | None] = mapped_column(Text, default=None)
    redirect_uris: Mapped[list[str]] = mapped_column(JSONB)
    grant_types: Mapped[list[str]] = mapped_column(JSONB)
    token_endpoint_auth_method: Mapped[str | None] = mapped_column(Text, default=None)
    scope: Mapped[str | None] = mapped_column(Text, default=None)
    client_name: Mapped[str | None] = mapped_column(Text, default=None)
