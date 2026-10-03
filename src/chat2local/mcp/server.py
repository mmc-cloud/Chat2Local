from mcp.server.mcpserver import MCPServer

from chat2local.mcp.instructions import MCP_INSTRUCTIONS
from chat2local.mcp.tools import register as register_tools


mcp = MCPServer("Chat2Local", instructions=MCP_INSTRUCTIONS)

register_tools(mcp)
