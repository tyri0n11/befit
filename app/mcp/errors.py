"""Domain-error -> MCP tool-error translation.

Mirrors the `_STATUS` HTTP-code maps in the REST endpoint modules, but MCP tool
results have no status code — a raised exception is surfaced to the client as
`CallToolResult(is_error=True, text=str(exc))` regardless of type (see
`MCPServer._handle_call_tool`). The error `code` is folded into the message so a
calling agent can still branch on it programmatically.
"""

from typing import NoReturn

from app.services.auth import AuthError
from app.services.template import TemplateError
from app.services.training import TrainingError
from mcp.server.mcpserver.exceptions import ToolError


def raise_tool_error(exc: TrainingError | TemplateError | AuthError) -> NoReturn:
    raise ToolError(f"{exc.code.value}: {exc.message}") from exc
