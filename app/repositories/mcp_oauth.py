"""Data access for registered MCP OAuth clients. The only layer that builds
queries — app/mcp/oauth.py must not import sqlalchemy.select itself."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp import OAuthClient


class McpOAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_client(self, client_id: str) -> OAuthClient | None:
        return await self.session.get(OAuthClient, client_id)

    async def create_client(
        self,
        client_id: str,
        client_secret: str | None,
        redirect_uris: list[str],
        grant_types: list[str],
        token_endpoint_auth_method: str | None,
        scope: str | None,
        client_name: str | None,
    ) -> OAuthClient:
        client = OAuthClient(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uris=redirect_uris,
            grant_types=grant_types,
            token_endpoint_auth_method=token_endpoint_auth_method,
            scope=scope,
            client_name=client_name,
        )
        self.session.add(client)
        # Flush rather than commit: the transaction is owned by db.session().
        await self.session.flush()
        return client
