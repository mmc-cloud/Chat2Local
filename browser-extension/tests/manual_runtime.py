"""Manual-only Hub/Agent and fixture harness in one isolated test process.

Uses the real production router, MCP server, AgentClient and BrowserBridge.
Does not stop the user's normal Agent or modify the user's normal config.
"""
import argparse
import asyncio
from contextlib import suppress
import logging
from pathlib import Path
import secrets
import threading

import uvicorn

from chat2local.agent.client import AgentClient
from chat2local.app import create_app
from chat2local.device.registry import DeviceRegistry
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.hub.router import DeviceRouter
from chat2local.runtime.config import BrowserConfig, load_config
from chat2local.runtime.supervisor import CoreServer, RuntimeSupervisor
from chat2local.runtime.workspace import WorkspaceManager
from manual_server import Handler, ThreadingHTTPServer


async def run(config_path: Path, directory: Path):
    config = load_config(config_path)
    if not config.browser.enabled:
        raise ValueError("Enable browser in the separate manual-test config first")
    workspace = WorkspaceManager(Path.cwd(), allowed_roots=config.security.allowed_roots or [Path.cwd()])
    agent = LocalToolDispatcher(workspace, config)
    hub = LocalToolDispatcher(workspace, config.model_copy(update={"browser": BrowserConfig()}))
    router = DeviceRouter("browser-smoke-hub", hub, DeviceRegistry("browser-smoke-hub"))
    hub_token = secrets.token_urlsafe(32)  # Independent of the browser credential; memory only.
    server = CoreServer(uvicorn.Config(create_app(router, hub_token=hub_token),
                                      host="127.0.0.1", port=18765, log_level="warning", access_log=False))
    client = AgentClient("ws://127.0.0.1:18765/device/ws", "browser-smoke-agent", hub_token, agent, proxy="direct")
    fixtures = ThreadingHTTPServer(("127.0.0.1", 18766), Handler)
    thread = threading.Thread(target=fixtures.serve_forever, daemon=True)
    thread.start()

    async def core():
        server_task = asyncio.create_task(server.serve())
        agent_task = None
        try:
            while not server.started:
                if server_task.done():
                    await server_task
                    return
                await asyncio.sleep(0.01)
            agent_task = asyncio.create_task(client.run())
            await server_task
        finally:
            if agent_task is not None:
                agent_task.cancel()
                with suppress(asyncio.CancelledError):
                    await agent_task
            if not server_task.done():
                server.should_exit = True
                await server_task

    supervisor = RuntimeSupervisor("hub", router.device_id, workspace.root,
        state=lambda: "running" if server.started else "starting", directory=directory,
        devices=router.list_devices)
    try:
        await supervisor.run(core, lambda task: setattr(server, "should_exit", True))
    finally:
        await asyncio.to_thread(fixtures.shutdown)
        fixtures.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--directory", type=Path,
                        default=Path.home() / ".chat2local/browser-smoke/phase2-runtime")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    asyncio.run(run(args.config, args.directory))
