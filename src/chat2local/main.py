"""Chat2Local entry point: bind the runtime, then start the MCP server.

Startup order matters: the workspace and the runtime config are resolved before
Uvicorn serves anything, so a bad workspace or config file fails loudly on
the command line instead of turning every later Tool call into an error.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import os
import socket
import sys
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from chat2local.mcp.tools import configure_config, configure_workspace, configure_router
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.device.registry import DeviceRegistry
from chat2local.hub.router import DeviceRouter
from chat2local.runtime.config import ConfigError, ManagementConfig, load_config, management_ports, validation_message
from chat2local.runtime.logging import configure_logging, websocket_callback_noise
from chat2local.runtime.process_manager import ProcessManager
from chat2local.runtime.shell import ShellError
from chat2local.runtime.instance import RuntimeManagementError
from chat2local.runtime.supervisor import CoreServer, RuntimeSupervisor
from chat2local.runtime.workspace import WorkspaceError, WorkspaceManager

logger = logging.getLogger(__name__)


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


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

    parser.add_argument("--device-id", default=argparse.SUPPRESS if inherited else None, help="Device identity (Hub/Agent: role config, then hostname).")
    parser.add_argument("--debug", action="store_true", default=argparse.SUPPRESS if inherited else False,
                        help="Enable safe project diagnostics (no authentication/frame dumps).")
    parser.add_argument("--web-ui", action=argparse.BooleanOptionalAction,
                        default=argparse.SUPPRESS if inherited else None,
                        help="Enable/disable the local Web UI (default: enabled for independent Core).")
    parser.add_argument("--management-port", type=int, default=argparse.SUPPRESS if inherited else None,
                        help="Local management HTTP port (default: management.port, then 8767).")
    parser.add_argument("--working-directory", default=argparse.SUPPRESS if inherited else None,
                        help="Set a validated working directory before starting Core (used for login startup).")


def _server_arguments(parser: argparse.ArgumentParser, *, inherited: bool = False) -> None:
    parser.add_argument("--host", default=argparse.SUPPRESS if inherited else None, help="HTTP listen address (Hub: hub.host, then 127.0.0.1).")
    parser.add_argument("--port", type=int, default=argparse.SUPPRESS if inherited else None, help="HTTP listen port (Hub: hub.port, then 8765).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chat2local", description="Single-user Chat2Local MCP agent")
    _runtime_arguments(parser)
    _server_arguments(parser)
    modes = parser.add_subparsers(dest="mode")
    hub = modes.add_parser("hub", help="Run MCP plus an authenticated Device WebSocket endpoint.")
    _runtime_arguments(hub, inherited=True)
    _server_arguments(hub, inherited=True)
    hub.add_argument("--token", default=None, help="Shared Hub token (or CHAT2LOCAL_HUB_TOKEN, then hub.token_file).")
    agent = modes.add_parser("agent", help="Connect outbound to a Hub; do not serve MCP.")
    _runtime_arguments(agent, inherited=True)
    agent.add_argument("--hub-url", default=None, help="Hub Device WebSocket URL (or agent.hub_url); use WSS on the public internet.")
    agent.add_argument("--token", default=None, help="Shared Hub token (or CHAT2LOCAL_HUB_TOKEN, then agent.token_file).")
    return parser


def _read_token_file(token_file: str, *, role: str) -> str:
    try:
        path = Path(token_file).expanduser()
    except RuntimeError:
        raise ConfigError(f"Could not expand {role.capitalize()} token_file home directory") from None
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise ConfigError(f"{role.capitalize()} token file does not exist: {path}") from None
    except (OSError, UnicodeDecodeError):
        raise ConfigError(f"Could not read {role.capitalize()} token file as UTF-8 text: {path}") from None
    if not token:
        raise ConfigError(f"{role.capitalize()} token file is empty: {path}")
    return token


def _resolve_token(cli_token: str | None, token_file: str | None, *, role: str) -> str:
    token = cli_token if cli_token is not None else os.environ.get("CHAT2LOCAL_HUB_TOKEN")
    if token is None and token_file is not None:
        token = _read_token_file(token_file, role=role)
    if not token:
        raise ConfigError(
            f"Hub token is required: use --token, CHAT2LOCAL_HUB_TOKEN or {role}.token_file in config.yaml"
        )
    return token


def _startup_command(args, settings, workspace: Path, config_path: Path | None, allowed_roots, token):
    """Replay this independent Core's startup without persisting transient secrets."""
    from chat2local.management.autostart import StartupCommand
    startup_error = None
    try:
        startup_cwd = WorkspaceManager(Path.cwd(), allowed_roots=allowed_roots).root
    except WorkspaceError:
        startup_cwd = workspace
        startup_error = "Start Core from an allowed working directory before enabling login startup"
    replay = []
    skip = False
    for argument in sys.argv[1:]:
        if skip:
            skip = False
            continue
        option = argument.split("=", 1)[0]
        if option in ("--workspace", "--config", "--working-directory", "--token"):
            skip = "=" not in argument
        else:
            replay.append(argument)
    if args.mode in ("hub", "agent"):
        role_config = settings.hub if args.mode == "hub" else settings.agent
        try:
            if args.token is None and os.environ.get("CHAT2LOCAL_HUB_TOKEN") is None:
                saved_token = token if role_config.token_file else None
            else:
                saved_token = _read_token_file(role_config.token_file, role=args.mode) if role_config.token_file else None
        except ConfigError:
            saved_token = None
        if saved_token != token:
            startup_error = "Save the current Hub/Agent token in its configured token_file before enabling login startup"
    prefix = [sys.executable, "--core"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "chat2local.main"]
    return StartupCommand(tuple([
        *prefix, *replay, "--workspace", str(workspace), "--working-directory", str(startup_cwd),
        *(["--config", str(config_path)] if config_path else []),
    ]), startup_cwd, startup_error)


def _attach_management(supervisor, settings, config_path, command, *, cli_port=None, cli_management=None) -> None:
    if settings is None or not settings.enabled:
        return
    from chat2local.management.adapter import CoreAdapter
    from chat2local.management.autostart import CoreAutostart
    from chat2local.management.http import create_management_app
    from chat2local.management.runtime import RuntimeClient
    saved = load_config(config_path)
    supervisor.management_ports = management_ports(
        saved.model_copy(update={"management": settings}), mode=supervisor.mode, cli_port=cli_port,
    )
    application = create_management_app(
        CoreAdapter(RuntimeClient(supervisor.directory), config_path=config_path), port=settings.port,
        stop=supervisor.request_stop, autostart=CoreAutostart(command, saved.autostart),
        mode=supervisor.mode, cli_port=cli_port, cli_management=cli_management,
        listener_port=lambda: supervisor.management.config.port,
    )
    supervisor.management = CoreServer(uvicorn.Config(
        application, host="127.0.0.1", port=settings.port, reload=False,
        proxy_headers=False, access_log=False, log_config=None,
    ))
    supervisor.management.on_started = lambda: logger.info("Local Web UI: http://127.0.0.1:%s/", supervisor.management.config.port)


async def _run_agent(client, *, management=None, config_path=None, command=None,
                     cli_port=None, cli_management=None) -> None:
    with websocket_callback_noise(asyncio.get_running_loop()):
        supervisor = RuntimeSupervisor(
            "agent", client.device_id, client.local.workspace.root, state=lambda: client.state,
            devices=client.list_devices,
            browser=client.local.browser.snapshot,
        )
        _attach_management(supervisor, management, config_path, command, cli_port=cli_port, cli_management=cli_management)
        await supervisor.run(client.run, lambda task: task.cancel())


async def _run_server(application, *, mode: str, device_id: str, workspace: Path,
                      host: str, port: int, management=None, config_path=None, command=None,
                      cli_port=None, cli_management=None) -> None:
    server = CoreServer(uvicorn.Config(application, host=host, port=port, reload=False))
    supervisor = RuntimeSupervisor(
        mode, device_id, workspace,
        state=lambda: "stopping" if server.should_exit else "running" if server.started else "starting",
        devices=application.state.router.list_devices,
        browser=application.state.router.local.browser.snapshot,
    )
    _attach_management(supervisor, management, config_path, command, cli_port=cli_port, cli_management=cli_management)
    await supervisor.run(server.serve, lambda task: setattr(server, "should_exit", True))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    token = None

    overrides = {
        "management": {"enabled": args.web_ui, "port": args.management_port},
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

    # The portable EXE continues to be controlled by its desktop shell.
    management = config.management
    if getattr(sys, "frozen", False) and args.web_ui is None:
        management = ManagementConfig(enabled=False, port=management.port)
    config_path = Path(args.config).expanduser().absolute() if args.config else None
    try:
        workspace = Path(args.workspace).expanduser() if args.workspace else Path.cwd()
        allowed_roots = config.security.allowed_roots or [workspace]
        workspace_manager = WorkspaceManager(workspace, allowed_roots=allowed_roots)
        if args.working_directory:
            selected = WorkspaceManager(args.working_directory, allowed_roots=allowed_roots)
            os.chdir(selected.root)
    except WorkspaceError as error:
        parser.error(str(error))
    except (OSError, RuntimeError, ValueError):
        parser.error("Could not resolve workspace or allowed roots")

    device_id = args.device_id
    host = args.host
    port = args.port
    if args.mode == "hub":
        if host is None:
            host = config.hub.host
        if port is None:
            port = config.hub.port
    if args.mode == "agent":
        hub_url = args.hub_url if args.hub_url is not None else config.agent.hub_url
        if not hub_url:
            parser.error("Agent Hub URL is required: use --hub-url or agent.hub_url in config.yaml")
    if args.mode in ("hub", "agent"):
        role_config = config.hub if args.mode == "hub" else config.agent
        if device_id is None:
            device_id = role_config.device_id
        try:
            token = _resolve_token(args.token, role_config.token_file, role=args.mode)
        except ConfigError as error:
            parser.error(str(error))
    if device_id is None:
        device_id = socket.gethostname()
    if host is None:
        host = "127.0.0.1"
    if port is None:
        port = 8765

    try:
        if management.enabled:
            management_ports(config, mode=args.mode or "standalone", cli_port=args.port)
    except ConfigError as error:
        parser.error(str(error))
    workspace_manager = configure_workspace(workspace_manager.root, allowed_roots=allowed_roots)
    configure_config(config)

    command = _startup_command(args, config, workspace_manager.root, config_path, allowed_roots, token)

    try:
        process_manager = ProcessManager(config.process)
    except ShellError as error:
        parser.error(str(error))
    local = LocalToolDispatcher(workspace_manager, config, process_manager)

    if args.mode == "agent":
        from chat2local.agent.client import AgentClient, RegistrationError
        try:
            client = AgentClient(hub_url, device_id, token, local, proxy=config.agent.proxy)
        except ValueError as error:
            parser.error(str(error))
        configure_logging(debug=args.debug, secrets=(token, hub_url, config.agent.proxy))
        try:
            asyncio.run(_run_agent(client, management=management, config_path=config_path, command=command,
                                   cli_port=args.port, cli_management=overrides["management"]))
        except KeyboardInterrupt:
            pass
        except RuntimeManagementError as error:
            parser.exit(1, f"{error}\n")
        except ConfigError as error:
            parser.error(str(error))
        except RegistrationError:
            # The runner already logged a safe, classified terminal rejection.
            parser.exit(1)
        except Exception:
            parser.exit(1, "Internal Agent error; see local logs\n")
        return

    try:
        registry = DeviceRegistry(device_id) if args.mode == "hub" else None
        router = DeviceRouter(device_id, local, registry)
    except ValidationError as error:
        parser.error(validation_message(error))
    except ValueError as error:
        parser.error(str(error))

    configure_router(router)
    from chat2local.app import create_app
    application = create_app(router, hub_token=token if args.mode == "hub" else None, auth=config.auth)

    role = "Hub" if args.mode == "hub" else "Standalone"
    configure_logging(debug=args.debug, secrets=(token,) if token else ())
    if config.auth.mode == "none" and not _is_loopback_host(host):
        logger.warning(
            "MCP authentication is disabled while listening on non-loopback address %s; "
            "protect /mcp with OAuth or a trusted external access-control layer",
            host,
        )
    logger.info("%s starting as %s", role, device_id)
    try:
        asyncio.run(_run_server(application, mode=args.mode or "standalone", device_id=device_id,
                                workspace=workspace_manager.root, host=host, port=port,
                                management=management, config_path=config_path, command=command,
                                cli_port=args.port, cli_management=overrides["management"]))
    except KeyboardInterrupt:
        pass
    except RuntimeManagementError as error:
        parser.exit(1, f"{error}\n")
    except Exception:
        logger.exception("Unexpected server runtime failure")
        parser.exit(1, "Internal server error; see local logs\n")
    finally:
        logger.info("%s stopped as %s", role, device_id)


if __name__ == "__main__":
    main()
