"""Chat2Local entry point: bind the runtime, then start the MCP server.

Design: docs/design-v0.1.md §4 Layer 1 — Local Tools.

Startup order matters: the workspace and the runtime config are resolved before
Uvicorn serves anything, so a bad workspace or config file fails loudly on
the command line instead of turning every later Tool call into an error.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
from pathlib import Path

import uvicorn

from chat2local.mcp.tools import configure_config, configure_workspace, configure_router
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.device.registry import DeviceRegistry
from chat2local.hub.router import DeviceRouter
from chat2local.runtime.config import ConfigError, load_config
from chat2local.runtime.process_manager import ProcessManager
from chat2local.runtime.shell import ShellError
from chat2local.runtime.workspace import WorkspaceError


def _runtime_arguments(parser: argparse.ArgumentParser, *, inherited: bool = False) -> None:
    parser.add_argument(
        "--workspace",
        default=argparse.SUPPRESS if inherited else None,
        help="Default workspace directory. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--config",
        default=argparse.SUPPRESS if inherited else None,
        help="Runtime config file to load (default: ~/.chat2local/config.yaml).",
    )
    parser.add_argument(
        "--read-max-lines",
        type=int,
        default=argparse.SUPPRESS if inherited else None,
        help="Override read.max_lines for this run.",
    )
    parser.add_argument(
        "--read-max-bytes",
        type=int,
        default=argparse.SUPPRESS if inherited else None,
        help="Override read.max_bytes for this run.",
    )
    parser.add_argument(
        "--search-max-results",
        type=int,
        default=argparse.SUPPRESS if inherited else None,
        help="Override search.max_results for this run.",
    )
    parser.add_argument(
        "--search-timeout",
        type=float,
        default=argparse.SUPPRESS if inherited else None,
        help="Override search.timeout (seconds) for this run.",
    )

    parser.add_argument("--device-id", default=argparse.SUPPRESS if inherited else socket.gethostname(), help="Device identity (default: hostname).")


def _server_arguments(parser: argparse.ArgumentParser, *, inherited: bool = False) -> None:
    parser.add_argument("--host", default=argparse.SUPPRESS if inherited else "127.0.0.1", help="HTTP listen address.")
    parser.add_argument("--port", type=int, default=argparse.SUPPRESS if inherited else 8765, help="HTTP listen port.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chat2local", description="Single-user Chat2Local MCP agent")
    _runtime_arguments(parser)
    _server_arguments(parser)
    modes = parser.add_subparsers(dest="mode")
    hub = modes.add_parser("hub", help="Run MCP plus an authenticated Device WebSocket endpoint.")
    _runtime_arguments(hub, inherited=True)
    _server_arguments(hub, inherited=True)
    hub.add_argument("--token", default=None, help="Shared Hub token (or CHAT2LOCAL_HUB_TOKEN).")
    agent = modes.add_parser("agent", help="Connect outbound to a Hub; do not serve MCP.")
    _runtime_arguments(agent, inherited=True)
    agent.add_argument("--hub-url", required=True, help="Hub Device WebSocket URL; use WSS on the public internet.")
    agent.add_argument("--token", default=None, help="Shared Hub token (or CHAT2LOCAL_HUB_TOKEN).")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    token = None
    if args.mode in ("hub", "agent"):
        token = args.token if args.token is not None else os.environ.get("CHAT2LOCAL_HUB_TOKEN")
        if not token:
            parser.error("Hub token is required: use --token or CHAT2LOCAL_HUB_TOKEN")

    overrides = {
        "read": {
            "max_lines": args.read_max_lines,
            "max_bytes": args.read_max_bytes,
        },
        "search": {
            "max_results": args.search_max_results,
            "timeout": args.search_timeout,
        },
    }

    # load_config drops the None entries, so an unset flag never hides a configured value.
    try:
        config = load_config(path=args.config, overrides=overrides)
    except ConfigError as error:
        parser.error(str(error))

    workspace = Path(args.workspace).expanduser() if args.workspace else Path.cwd()
    allowed_roots = config.security.allowed_roots or [workspace]

    try:
        workspace_manager = configure_workspace(workspace, allowed_roots=allowed_roots)
    except WorkspaceError as error:
        parser.error(str(error))

    configure_config(config)

    try:
        process_manager = ProcessManager(config.process)
    except ShellError as error:
        parser.error(str(error))
    local = LocalToolDispatcher(workspace_manager, config, process_manager)

    if args.mode == "agent":
        from chat2local.agent.client import AgentClient
        try:
            client = AgentClient(args.hub_url, args.device_id, token, local)
        except ValueError as error:
            parser.error(str(error))
        logging.basicConfig(level=logging.INFO)
        try:
            asyncio.run(client.run())
        except KeyboardInterrupt:
            pass
        return

    try:
        registry = DeviceRegistry(args.device_id) if args.mode == "hub" else None
        router = DeviceRouter(args.device_id, local, registry)
    except ValueError as error:
        parser.error(str(error))

    configure_router(router)
    if args.mode == "hub":
        from chat2local.app import create_app
        application = create_app(router, hub_token=token)
    else:
        application = "chat2local.app:app"

    uvicorn.run(
        application,
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
