"""The MCP server instance. Tool modules register themselves as a side effect
of being imported below — same "import for side effect" shape the project
already accepts for `__init__.py` re-exports.
"""

from app.core.settings import settings
from app.mcp.oauth import BefitOAuthProvider
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    name="befit",
    auth_server_provider=BefitOAuthProvider(),
    auth=AuthSettings(
        issuer_url=settings.MCP_ISSUER_URL,
        resource_server_url=f"{settings.MCP_ISSUER_URL}/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=False),
    ),
)

from app.mcp.tools import catalog, stats, template, training
