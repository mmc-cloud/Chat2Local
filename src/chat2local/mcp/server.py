from mcp.server.mcpserver import MCPServer

from chat2local.mcp.tools import register as register_tools


mcp = MCPServer("Chat2Local")

register_tools(mcp)
