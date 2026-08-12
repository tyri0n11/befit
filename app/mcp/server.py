"""The MCP server instance. Tool modules register themselves as a side effect
of being imported below — same "import for side effect" shape the project
already accepts for `__init__.py` re-exports.
"""

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(name="befit")

from app.mcp.tools import catalog, stats, template, training
