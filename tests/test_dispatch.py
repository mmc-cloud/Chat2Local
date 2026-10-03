"""Standalone routing and extensible device-local dispatch."""

import socket
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from chat2local.dispatch.local import LocalToolDispatcher, ToolExecutionError
from chat2local.hub.router import DeviceRouter
from chat2local.mcp.tools import register
from chat2local.runtime.config import AppConfig, ReadConfig
from chat2local.runtime.workspace import WorkspaceManager
from conftest import run
from test_mcp_surface import payload


def make_dispatcher(root: Path) -> LocalToolDispatcher:
    return LocalToolDispatcher(WorkspaceManager(root), AppConfig())


def test_dispatcher_unknown_and_registration(tmp_path: Path) -> None:
    dispatcher = make_dispatcher(tmp_path)
    with pytest.raises(ToolExecutionError, match="Unknown local tool"):
        run(dispatcher.execute("missing", {}))

    async def echo(arguments):
        return arguments

    dispatcher.register("echo", echo)
    assert run(dispatcher.execute("echo", {"value": 42})) == {"value": 42}
    with pytest.raises(ValueError, match="duplicate"):
        dispatcher.register("echo", echo)


@pytest.mark.parametrize("arguments", [
    {"path": "file.txt", "allowed_roots": []},
    {"path": "file.txt", "max_bytes": 999999},
    {"path": "file.txt", "device": "other"},
    {"path": 12},
    {},
])
def test_dispatcher_rejects_untrusted_arguments(tmp_path: Path, arguments) -> None:
    with pytest.raises(ToolExecutionError, match="Invalid arguments"):
        run(make_dispatcher(tmp_path).execute("read", arguments))


def test_dispatcher_instances_keep_device_config_separate(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("a\nb\n", encoding="utf-8", newline="")
    small = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig(read=ReadConfig(max_lines=1)))
    normal = make_dispatcher(tmp_path)
    assert run(small.execute("read", {"path": "file.txt"}))["lines_returned"] == 1
    assert run(normal.execute("read", {"path": "file.txt"}))["lines_returned"] == 2


@pytest.mark.parametrize("device", [None, socket.gethostname()])
@pytest.mark.parametrize("tool,arguments", [
    ("read", {"path": "file.txt"}),
    ("search", {"query": "needle", "mode": "content"}),
])
def test_standalone_local_tools(tmp_path: Path, device, tool, arguments) -> None:
    (tmp_path / "file.txt").write_text("needle\n", encoding="utf-8", newline="")
    router = DeviceRouter(socket.gethostname(), make_dispatcher(tmp_path))
    server = MCPServer("Standalone")
    register(server, router)
    result = payload(run(server.call_tool(tool, {**arguments, "device": device})))
    assert "needle" in str(result)


@pytest.mark.parametrize("tool,arguments", [
    ("read", {"path": "file.txt"}),
    ("search", {"query": "needle", "mode": "content"}),
])
def test_standalone_other_device_is_unavailable(tmp_path: Path, tool, arguments) -> None:
    server = MCPServer("Standalone")
    register(server, DeviceRouter("local", make_dispatcher(tmp_path)))
    with pytest.raises(ToolError, match="Device unavailable in standalone mode"):
        run(server.call_tool(tool, {**arguments, "device": "remote"}))


def test_list_devices_standalone(tmp_path: Path) -> None:
    server = MCPServer("Standalone")
    register(server, DeviceRouter("desktop", make_dispatcher(tmp_path)))
    assert payload(run(server.call_tool("list_devices", {}))) == {"devices": [{
        "device_id": "desktop", "kind": "local", "online": True, "tools": ["read", "search", "apply_patch", "exec_command", "interact_process", "kill_process", "handoff_list", "handoff_get", "handoff_save"],
    }]}
    tools = run(server.list_tools())
    assert {tool.name for tool in tools} == {"read", "search", "apply_patch", "exec_command", "interact_process", "kill_process", "handoff_list", "handoff_get", "handoff_save", "list_devices"}
    listing = next(tool for tool in tools if tool.name == "list_devices")
    assert listing.input_schema["properties"] == {}
    for tool in tools:
        if tool.name != "list_devices":
            assert tool.input_schema["properties"]["device"]["default"] is None
