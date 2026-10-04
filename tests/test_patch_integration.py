"""Dispatcher, MCP wrappers and real HTTP MCP -> Hub -> WebSocket -> Agent."""

import asyncio
import errno
from pathlib import Path
import threading

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError

from chat2local.agent.client import AgentClient
from chat2local.app import create_app
from chat2local.device.registry import DeviceError
from chat2local.dispatch.local import LocalToolDispatcher, ToolExecutionError
from chat2local.hub.router import DeviceRouter
from chat2local.mcp.server import create_mcp_server
from chat2local.runtime.config import AppConfig
from chat2local.runtime.workspace import WorkspaceManager
from conftest import run
from test_apply_patch import create_link, patch, update
from test_devices import TOKEN, decode, eventually, hub_runtime, running_agent, running_server
from test_mcp_surface import payload


def test_dispatcher_patch_validation_and_workspace_override(tmp_path):
    a, b, outside = tmp_path / "A", tmp_path / "B", tmp_path.parent / (tmp_path.name + "-outside")
    for path in (a, b, outside):
        path.mkdir()
    manager = WorkspaceManager(a, allowed_roots=[tmp_path])
    dispatcher = LocalToolDispatcher(manager, AppConfig())
    assert dispatcher.tools == ("read", "search", "apply_patch", "exec_command", "interact_process", "kill_process", "handoff_list", "handoff_get", "handoff_save")
    body = patch("*** Add File: new.txt\n+new")
    result = run(dispatcher.execute("apply_patch", {"patch": body, "workspace": str(b)}))
    assert result["success"] and (b / "new.txt").exists() and not (a / "new.txt").exists()
    assert run(dispatcher.execute("apply_patch", {"patch": body}))["success"]
    assert (a / "new.txt").exists() and dispatcher.workspace is manager
    for arguments, message in (
        ({"patch": body, "allowed_roots": []}, "Invalid arguments"),
        ({"patch": 1}, "Invalid arguments"),
        ({}, "Invalid arguments"),
        ({"patch": body, "device": "other"}, "Invalid arguments"),
        ({"patch": body, "workspace": "B"}, "absolute path"),
        ({"patch": body, "workspace": str(outside)}, "outside allowed roots"),
        ({"patch": patch("*** Delete File: ../A/new.txt"), "workspace": str(b)}, "outside workspace"),
    ):
        with pytest.raises(ToolExecutionError, match=message):
            run(dispatcher.execute("apply_patch", arguments))
    assert (a / "new.txt").read_bytes() == b"new\n"


def test_mcp_validation_is_tool_error_and_commit_failure_is_truthful_result(tmp_path, monkeypatch):
    dispatcher = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
    server = create_mcp_server(DeviceRouter("local", dispatcher))
    with pytest.raises(ToolError, match="does not exist"):
        run(server.call_tool("apply_patch", {"patch": patch("*** Delete File: missing")}))
    with pytest.raises(ToolError, match="unavailable"):
        run(server.call_tool("apply_patch", {"patch": patch("*** Add File: x\n+x"), "device": "remote"}))
    (tmp_path / "file.txt").write_bytes(b"old\n")
    from chat2local.tools import apply_patch as module
    def fail(*args):
        raise PermissionError(13, "Denied")
    monkeypatch.setattr(module.os, "replace", fail)
    result = payload(run(server.call_tool("apply_patch", {"patch": patch(update())})))
    assert not result["success"] and not result["partial"] and result["error"] == "Denied"
    assert (tmp_path / "file.txt").read_bytes() == b"old\n"


@pytest.mark.parametrize("remote", [False, True])
def test_real_standalone_and_hub_agent_patch_tools(tmp_path: Path, remote, monkeypatch):
    hub = tmp_path / "hub"
    allowed = tmp_path / "agent-allowed"
    a, b = allowed / "A", allowed / "B"
    outside = tmp_path / "outside"
    for directory in (hub, a, b, outside):
        directory.mkdir(parents=True)
        (directory / "file.txt").write_bytes(b"old\n")
        (directory / "remove.txt").write_bytes(b"remove\n")
        (directory / "dir").mkdir()
        (directory / "dir/child").write_bytes(b"child")
    target_root = a if remote else hub
    selected = b if remote else hub
    device = "desktop" if remote else None
    executions = []
    from chat2local.dispatch import local as local_module
    original = local_module.apply_patch_impl
    async def observe(workspace, text):
        executions.append(workspace.root)
        return await original(workspace, text)
    monkeypatch.setattr(local_module, "apply_patch_impl", observe)

    async def exercise(client):
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == {"read", "search", "apply_patch", "exec_command", "interact_process", "kill_process", "handoff_list", "handoff_get", "handoff_save", "list_devices"}
        schema = next(tool.input_schema for tool in tools if tool.name == "apply_patch")
        assert set(schema["properties"]) == {"patch", "workspace", "device"}
        assert schema["required"] == ["patch"]
        listing = decode(await client.call_tool("list_devices", {}))
        assert all("apply_patch" in entry["tools"] for entry in listing["devices"])
        body = ("*** Add File: new/note.txt\n+hello\n" + update() +
                "\n*** Move to: renamed.txt\n*** Delete File: remove.txt\n"
                "*** Move Directory: dir\n*** Move to: moved-dir")
        result = decode(await client.call_tool("apply_patch", {"patch": patch(body), "device": device}))
        assert result["success"] and [step["action"] for step in result["operations"]] == [
            "add", "update", "move", "delete", "move_directory"]
        assert (target_root / "renamed.txt").read_bytes() == b"new\n"
        assert (target_root / "moved-dir/child").read_bytes() == b"child"
        assert not (target_root / "file.txt").exists() and not (target_root / "remove.txt").exists()
        assert str(target_root) not in str(result)
        assert decode(await client.call_tool("read", {"path": "renamed.txt", "device": device}))["text"] == "new\n"
        searched = decode(await client.call_tool("search", {
            "query": "hello", "mode": "content", "device": device,
        }))
        assert searched["result_count"] == 1
        assert decode(await client.call_tool("apply_patch", {
            "patch": patch("*** Add File: selected.txt\n+selected"), "workspace": str(selected), "device": device,
        }))["success"]
        assert (selected / "selected.txt").read_bytes() == b"selected\n"
        assert decode(await client.call_tool("apply_patch", {
            "patch": patch("*** Add File: default.txt\n+default"), "device": device,
        }))["success"]
        assert (target_root / "default.txt").exists()
        before = len(executions)
        error = await client.call_tool("apply_patch", {
            "patch": patch("*** Add File: must-not-exist\n+x\n*** Delete File: missing"), "device": device,
        })
        assert error.is_error and "does not exist" in error.content[0].text
        assert len(executions) == before + 1  # A side-effect request is never replayed.
        assert not (target_root / "must-not-exist").exists()
        for body, ws, message in (
            ("*** Add File: denied\n+x", str(outside), "outside allowed roots"),
            ("*** Add File: ../escape\n+x", None, "outside workspace"),
            (f"*** Delete File: {outside / 'file.txt'}", None, "outside workspace"),
            ("*** Add File: denied\n+x", "relative", "absolute path"),
        ):
            error = await client.call_tool("apply_patch", {"patch": patch(body), "workspace": ws, "device": device})
            assert error.is_error and message in error.content[0].text
        assert (outside / "file.txt").read_bytes() == b"old\n"
        if remote:
            error = await client.call_tool("apply_patch", {
                "patch": patch("*** Delete File: ../A/default.txt"), "workspace": str(b), "device": device,
            })
            assert error.is_error and (a / "default.txt").exists()
            assert (hub / "file.txt").read_bytes() == b"old\n"

    async def scenario():
        if remote:
            router = hub_runtime(hub)
            local = LocalToolDispatcher(WorkspaceManager(a, allowed_roots=[allowed]), AppConfig())
            async with running_server(create_app(router, hub_token=TOKEN)) as (url, ws_url):
                async with running_agent(AgentClient(ws_url, "desktop", TOKEN, local)):
                    await eventually(lambda: "desktop" in router.registry.sessions)
                    async with streamable_http_client(url + "/mcp/") as streams:
                        async with ClientSession(streams[0], streams[1]) as client:
                            await client.initialize()
                            await exercise(client)
                    assert local.workspace.root == a.resolve()
        else:
            router = DeviceRouter("local", LocalToolDispatcher(WorkspaceManager(hub), AppConfig()))
            async with running_server(create_app(router)) as (url, _):
                async with streamable_http_client(url + "/mcp/") as streams:
                    async with ClientSession(streams[0], streams[1]) as client:
                        await client.initialize()
                        await exercise(client)
        assert router.local.workspace.root == hub.resolve()
    run(scenario())


def test_remote_commit_partial_result_is_preserved(tmp_path, monkeypatch):
    hub, agent_root = tmp_path / "hub", tmp_path / "agent"
    hub.mkdir()
    agent_root.mkdir()
    (agent_root / "file.txt").write_bytes(b"old\n")
    from chat2local.tools import apply_patch as module
    def fail(*args):
        raise PermissionError(errno.EACCES, "Denied")
    monkeypatch.setattr(module.os, "replace", fail)

    async def scenario():
        router = hub_runtime(hub)
        local = LocalToolDispatcher(WorkspaceManager(agent_root), AppConfig())
        async with running_server(create_app(router, hub_token=TOKEN)) as (url, ws_url):
            async with running_agent(AgentClient(ws_url, "desktop", TOKEN, local)):
                await eventually(lambda: "desktop" in router.registry.sessions)
                async with streamable_http_client(url + "/mcp/") as streams:
                    async with ClientSession(streams[0], streams[1]) as client:
                        await client.initialize()
                        result = decode(await client.call_tool("apply_patch", {
                            "patch": patch("*** Add File: first\n+first\n" + update()), "device": "desktop",
                        }))
                        assert not result["success"] and result["partial"] and result["error"] == "Denied"
                        assert result["operations"] == [{"action": "add", "path": "first"}]
                        assert result["failed_operation"] == {"action": "update", "path": "file.txt"}
        assert (agent_root / "first").read_bytes() == b"first\n"
        assert (agent_root / "file.txt").read_bytes() == b"old\n" and list(hub.iterdir()) == []
    run(scenario())


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_real_remote_patch_cannot_escape_through_links(tmp_path, kind):
    hub, agent_root, outside = tmp_path / "hub", tmp_path / "agent", tmp_path / "outside"
    for directory in (hub, agent_root, outside):
        directory.mkdir()
    (outside / "secret").write_bytes(b"keep")
    create_link(agent_root / "link", outside, kind)

    async def scenario():
        router = hub_runtime(hub)
        local = LocalToolDispatcher(WorkspaceManager(agent_root), AppConfig())
        async with running_server(create_app(router, hub_token=TOKEN)) as (url, ws_url):
            async with running_agent(AgentClient(ws_url, "desktop", TOKEN, local)):
                await eventually(lambda: "desktop" in router.registry.sessions)
                async with streamable_http_client(url + "/mcp/") as streams:
                    async with ClientSession(streams[0], streams[1]) as client:
                        await client.initialize()
                        for body in ("*** Add File: link/new\n+new", "*** Delete File: link/secret"):
                            result = await client.call_tool("apply_patch", {"patch": patch(body), "device": "desktop"})
                            assert result.is_error and "outside workspace" in result.content[0].text
        assert (outside / "secret").read_bytes() == b"keep" and not (outside / "new").exists()
    run(scenario())


def test_real_remote_timeout_and_reconnect_never_replay_patch(tmp_path, monkeypatch):
    hub, agent_root = tmp_path / "hub", tmp_path / "agent"
    hub.mkdir()
    agent_root.mkdir()
    from chat2local.tools import apply_patch as module
    original = module.commit_patch
    entered, release = threading.Event(), threading.Event()
    executions = []
    def delayed_commit(plan):
        executions.append(plan)
        entered.set()
        if not release.wait(5):
            raise AssertionError("Test failed to release commit worker")
        return original(plan)
    monkeypatch.setattr(module, "commit_patch", delayed_commit)

    async def scenario():
        router = hub_runtime(hub, timeout=0.1)
        local = LocalToolDispatcher(WorkspaceManager(agent_root), AppConfig())
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, ws_url):
            async with running_agent(AgentClient(ws_url, "desktop", TOKEN, local)):
                await eventually(lambda: "desktop" in router.registry.sessions)
                session = router.registry.get("desktop")
                try:
                    task = asyncio.create_task(router.execute("apply_patch", {
                        "patch": patch("*** Add File: once\n+once"),
                    }, "desktop"))
                    await eventually(entered.is_set)
                    with pytest.raises(DeviceError, match="timed out"):
                        await task
                    assert session.pending == {}
                    await session.websocket.close(code=1001)
                    await eventually(lambda: router.registry.sessions.get("desktop") is not session)
                    release.set()
                    await eventually(lambda: (agent_root / "once").exists())
                    await eventually(lambda: router.registry.sessions.get("desktop") is not None
                                     and router.registry.sessions["desktop"] is not session)
                    assert len(executions) == 1
                    assert await router.execute("read", {"path": "once"}, "desktop")
                    assert len(executions) == 1 and (agent_root / "once").read_bytes() == b"once\n"
                finally:
                    release.set()
    run(scenario())
