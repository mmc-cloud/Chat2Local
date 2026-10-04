from mcp.server.mcpserver import MCPServer
from mcp.server.auth.settings import AuthSettings

from chat2local.hub.router import DeviceRouter
from chat2local.mcp.instructions import MCP_INSTRUCTIONS
from chat2local.mcp.tools import register as register_tools
from chat2local.runtime.config import AuthConfig


def create_mcp_server(router: DeviceRouter | None = None, *, auth: AuthConfig | None = None) -> MCPServer:
    """The single MCP construction point, with one registration per instance."""
    options = {}
    if auth is not None and auth.mode == "oauth":
        from chat2local.auth.workos import WorkOSTokenVerifier

        options = {
            "token_verifier": WorkOSTokenVerifier(auth.issuer_url, auth.resource_server_url),
            "auth": AuthSettings(
                issuer_url=auth.issuer_url, resource_server_url=auth.resource_server_url,
                validate_token_resource=True, required_scopes=[],
            ),
        }
    server = MCPServer("Chat2Local", instructions=MCP_INSTRUCTIONS, **options)
    register_tools(server, router)
    return server


# Compatibility for callers importing the default, dynamically bound server.
mcp = create_mcp_server()
