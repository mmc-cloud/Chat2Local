"""Standalone HTTP app and optional Hub app factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from mcp.server.mcpserver import MCPServer

from chat2local.hub.router import DeviceRouter
from chat2local.hub.websocket import serve_device
from chat2local.mcp.instructions import MCP_INSTRUCTIONS
from chat2local.mcp.server import mcp
from chat2local.mcp.tools import get_router, register


def create_app(router: DeviceRouter | None = None, *, hub_token: str | None = None) -> FastAPI:
    if hub_token is not None and (not hub_token or router is None or router.registry is None):
        raise ValueError("Hub requires a token and a device registry")
    server = mcp if router is None else MCPServer("Chat2Local", instructions=MCP_INSTRUCTIONS)
    if router is not None:
        register(server, router)
    mcp_app = server.streamable_http_app(streamable_http_path="/", json_response=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        local_router = router if router is not None else get_router()
        async with server.session_manager.run():
            try:
                yield
            finally:
                try:
                    if local_router.registry is not None:
                        await local_router.registry.close()
                finally:
                    await local_router.local.process_manager.shutdown()

    application = FastAPI(title="Chat2Local", version="0.1.0", lifespan=lifespan)
    application.state.router = router
    application.state.mcp = server

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    if hub_token is not None:
        @application.websocket("/device/ws")
        async def device_socket(websocket: WebSocket) -> None:
            await serve_device(websocket, router.registry, hub_token)

    application.mount("/mcp", mcp_app)
    return application


app = create_app()
