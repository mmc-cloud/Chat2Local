"""Real HTTP MCP and WebSocket Hub/Agent integration, plus lifecycle checks."""

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import socket
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError
from websockets.asyncio.client import connect

from chat2local.agent.client import AgentClient
from chat2local.app import create_app
from chat2local.device.registry import DeviceError, DeviceRegistry, DeviceSession
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.hub.router import DeviceRouter
from chat2local.protocol.messages import PROTOCOL_VERSION, Hello, HelloAck, Request, Response
from chat2local.runtime.config import AppConfig, ReadConfig
from chat2local.runtime.workspace import WorkspaceManager
from conftest import run

TOKEN = "test-shared-token"


async def eventually(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@asynccontextmanager
async def running_server(app, port=0):
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="critical", access_log=False,
        ws="websockets-sansio", timeout_graceful_shutdown=1,
    ))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        await eventually(lambda: server.started or task.done())
        if task.done():
            await task
            raise AssertionError("Server exited during startup")
        yield f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}/device/ws"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 5)
        finally:
            listener.close()


@asynccontextmanager
async def running_agent(agent):
    task = asyncio.create_task(agent.run())
    try:
        yield task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def hub_runtime(root: Path, *, timeout=30):
    local = LocalToolDispatcher(WorkspaceManager(root), AppConfig())
    registry = DeviceRegistry("hub")
    return DeviceRouter("hub", local, registry, timeout=timeout)


async def hello(ws, device="desktop", token=TOKEN, capabilities=None):
    await ws.send(Hello(protocol_version=PROTOCOL_VERSION, device_id=device, token=token,
                        capabilities=capabilities or ["read", "search"]).model_dump_json())
    return HelloAck.model_validate_json(await ws.recv())


def decode(result):
    assert not result.is_error, result
    return json.loads(result.content[0].text)


def test_real_standalone_mcp_health_and_tools(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        router = DeviceRouter("desktop", LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig()))
        async with running_server(create_app(router)) as (url, _):
            async with streamable_http_client(url + "/mcp/") as streams:
                async with ClientSession(streams[0], streams[1]) as client:
                    await client.initialize()
                    assert decode(await client.call_tool("read", {"path": "file.txt"}))["text"] == "needle"
                    assert decode(await client.call_tool("search", {"query": "needle", "mode": "content"}))["result_count"] == 1
                    assert len(decode(await client.call_tool("list_devices", {}))["devices"]) == 1
                    error = await client.call_tool("read", {"path": "file.txt", "device": "remote"})
                    assert error.is_error and "unavailable" in error.content[0].text
            import httpx2 as httpx
            async with httpx.AsyncClient() as client:
                assert (await client.get(url + "/health")).json() == {"status": "ok"}
                # Standalone does not expose the Agent registration endpoint.
                assert (await client.get(url + "/device/ws")).status_code == 404

    run(scenario())


def test_hub_agent_mcp_read_search_and_local_permissions(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    allowed = tmp_path / "agent-allowed"
    workspace = allowed / "A"
    selected = allowed / "B"
    outside = tmp_path / "outside"
    for directory in (hub, workspace, selected, outside):
        directory.mkdir(parents=True)
        (directory / "file.txt").write_text(f"needle {directory.name}\nsecond\n", encoding="utf-8", newline="")

    async def scenario():
        router = hub_runtime(hub)
        local = LocalToolDispatcher(WorkspaceManager(workspace, allowed_roots=[allowed]),
                                    AppConfig(read=ReadConfig(max_lines=1)))
        async with running_server(create_app(router, hub_token=TOKEN)) as (url, ws_url):
            async with running_agent(AgentClient(ws_url, "desktop", TOKEN, local)):
                await eventually(lambda: "desktop" in router.registry.sessions)
                async with streamable_http_client(url + "/mcp/") as streams:
                    async with ClientSession(streams[0], streams[1]) as client:
                        await client.initialize()
                        devices = decode(await client.call_tool("list_devices", {}))["devices"]
                        assert {entry["device_id"] for entry in devices} == {"hub", "desktop"}
                        assert all(entry["online"] and entry["tools"] == ["read", "search", "apply_patch", "exec_command", "interact_process", "kill_process", "handoff_list", "handoff_get", "handoff_save"] for entry in devices)
                        assert {entry["kind"] for entry in devices} == {"local", "remote"}
                        for device in (None, "hub"):
                            result = decode(await client.call_tool("read", {"path": "file.txt", "device": device}))
                            assert "hub" in result["text"] and result["lines_returned"] == 2
                        remote = decode(await client.call_tool("read", {"path": "file.txt", "device": "desktop"}))
                        assert remote["text"] == "needle A\n" and remote["lines_returned"] == 1
                        for mode in ("name", "content"):
                            result = decode(await client.call_tool("search", {
                                "query": "file" if mode == "name" else "needle", "mode": mode, "device": "desktop",
                            }))
                            assert result["result_count"] == 1
                            assert result["results"][0]["path"] == "file.txt"
                        for tool, extra in (("read", {}), ("search", {"query": "needle", "mode": "content"})):
                            override = decode(await client.call_tool(tool, {
                                "path": "file.txt", "workspace": str(selected), "device": "desktop", **extra,
                            }))
                            assert "needle B" in str(override)
                            for path, ws, message in (
                                ("file.txt", str(outside), "outside allowed roots"),
                                ("file.txt", str(hub), "outside allowed roots"),
                                ("../A/file.txt", str(selected), "outside workspace"),
                                (str(workspace / "file.txt"), str(selected), "outside workspace"),
                                ("file.txt", "B", "absolute path"),
                            ):
                                result = await client.call_tool(tool, {"path": path, "workspace": ws,
                                                                       "device": "desktop", **extra})
                                assert result.is_error and message in result.content[0].text
                        again = decode(await client.call_tool("read", {"path": "file.txt", "device": "desktop"}))
                        assert again == remote
                        assert router.local.workspace.root == hub.resolve()
                        assert local.workspace.root == workspace.resolve()
                        assert local.workspace.allowed_roots == (allowed.resolve(),)
            await eventually(lambda: "desktop" not in router.registry.sessions)
            with pytest.raises(DeviceError, match="offline"):
                await router.execute("read", {"path": "file.txt"}, "desktop")

    run(scenario())


@pytest.mark.parametrize("device,token,message", [
    ("desktop", "wrong", "Invalid Hub token"),
    ("hub", TOKEN, "conflicts"),
    ("desktop", TOKEN, "already connected"),
])
def test_registration_rejects_token_or_duplicate_identity(tmp_path, device, token, message):
    async def scenario():
        router = hub_runtime(tmp_path)
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, url):
            async with connect(url) as original:
                assert (await hello(original)).ok
                session = router.registry.get("desktop")
                async with connect(url) as rejected:
                    ack = await hello(rejected, device, token)
                    assert not ack.ok and message in ack.error
                assert router.registry.get("desktop") is session
                assert len(router.registry.sessions) == 1
            await eventually(lambda: not router.registry.sessions)

    run(scenario())


@pytest.mark.parametrize("raw", [
    "not-json", '{"type":"hello","protocol_version":2}',
    '{"type":"hello","device_id":"x","token":"PRIVATE","capabilities":["read"]}',
    '{"type":"hello","protocol_version":1,"device_id":"x","token":"PRIVATE","capabilities":"read"}',
])
def test_bad_protocol_hello_does_not_register_or_echo_token(tmp_path, raw):
    async def scenario():
        router = hub_runtime(tmp_path)
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, url):
            async with connect(url) as ws:
                await ws.send(raw)
                ack = HelloAck.model_validate_json(await ws.recv())
                assert not ack.ok
                assert "PRIVATE" not in ack.error
                assert router.registry.sessions == {}

    run(scenario())


def test_pending_timeout_late_response_and_disconnect_cleanup(tmp_path):
    async def scenario():
        router = hub_runtime(tmp_path, timeout=0.05)
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, url):
            async with connect(url) as ws:
                assert (await hello(ws)).ok
                session = router.registry.get("desktop")
                task = asyncio.create_task(router.execute("read", {"path": "."}, "desktop"))
                request = Request.model_validate_json(await ws.recv())
                assert request.request_id.version == 4
                with pytest.raises(DeviceError, match="timed out"):
                    await task
                assert session.pending == {}
                await ws.send(Response(protocol_version=PROTOCOL_VERSION, request_id=request.request_id, ok=True, result={"late": True}).model_dump_json())
                router.timeout = 5
                tasks = [asyncio.create_task(router.execute("read", {"path": "."}, "desktop")) for _ in range(3)]
                requests = [Request.model_validate_json(await ws.recv()) for _ in range(3)]
                assert len({req.request_id for req in requests}) == 3
                assert len(session.pending) == 3
                await ws.close()
                for task in tasks:
                    with pytest.raises(DeviceError, match="offline"):
                        await asyncio.wait_for(task, 1)
                assert session.pending == {}
                await eventually(lambda: not router.registry.sessions)
            for device in ("desktop", "missing"):
                with pytest.raises(DeviceError, match="offline"):
                    await router.execute("read", {"path": "."}, device)

    run(scenario())


def test_concurrent_remote_responses_match_request_ids(tmp_path):
    async def scenario():
        router = hub_runtime(tmp_path)
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, url):
            async with connect(url) as ws:
                await hello(ws)
                tasks = [asyncio.create_task(router.execute("read", {"path": str(i)}, "desktop")) for i in range(3)]
                requests = [Request.model_validate_json(await ws.recv()) for _ in range(3)]
                for req in reversed(requests):
                    await ws.send(Response(protocol_version=PROTOCOL_VERSION, request_id=req.request_id, ok=True, result=req.arguments).model_dump_json())
                assert await asyncio.gather(*tasks) == [{"path": str(i)} for i in range(3)]
                assert router.registry.get("desktop").pending == {}

    run(scenario())


def test_agent_reconnects_and_tools_remain_generic(tmp_path):
    async def scenario():
        router = hub_runtime(tmp_path)
        local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())

        async def echo(arguments):
            return arguments

        local.register("echo", echo)
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, url):
            async with running_agent(AgentClient(url, "desktop", TOKEN, local)):
                await eventually(lambda: "desktop" in router.registry.sessions)
                session = router.registry.get("desktop")
                assert await router.execute("echo", {"value": 7}, "desktop") == {"value": 7}
                with pytest.raises(DeviceError, match="does not support"):
                    await router.execute("unknown", {}, "desktop")
                await session.websocket.close(code=1001)
                await eventually(lambda: router.registry.sessions.get("desktop") is not None
                                 and router.registry.sessions["desktop"] is not session)
                assert await router.execute("echo", {"value": 8}, "desktop") == {"value": 8}

    run(scenario())


def test_hub_restart_has_empty_registry_and_agent_reconnects(tmp_path):
    (tmp_path / "file.txt").write_text("after restart", encoding="utf-8")

    async def scenario():
        from urllib.parse import urlsplit
        first = hub_runtime(tmp_path)
        task = None
        try:
            async with running_server(create_app(first, hub_token=TOKEN)) as (_, url):
                port = urlsplit(url).port
                local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
                task = asyncio.create_task(AgentClient(url, "desktop", TOKEN, local).run())
                await eventually(lambda: "desktop" in first.registry.sessions)
                original = first.registry.get("desktop")
            assert first.registry.sessions == {} and original.closed
            second = hub_runtime(tmp_path)
            assert second.registry.sessions == {}
            async with running_server(create_app(second, hub_token=TOKEN), port=port):
                await eventually(lambda: "desktop" in second.registry.sessions, timeout=10)
                result = await second.execute("read", {"path": "file.txt"}, "desktop")
                assert result["text"] == "after restart"
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    run(scenario())


def test_two_agents_are_routed_independently(tmp_path):
    roots = [tmp_path / "desktop", tmp_path / "nas"]
    for root in roots:
        root.mkdir()
        (root / "file.txt").write_text(root.name, encoding="utf-8")

    async def scenario():
        router = hub_runtime(tmp_path)
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, url):
            clients = [AgentClient(url, root.name, TOKEN,
                                   LocalToolDispatcher(WorkspaceManager(root), AppConfig())) for root in roots]
            async with running_agent(clients[0]), running_agent(clients[1]):
                await eventually(lambda: len(router.registry.sessions) == 2)
                assert len(router.list_devices()["devices"]) == 3
                results = await asyncio.gather(*(
                    router.execute("read", {"path": "file.txt"}, root.name) for root in roots
                ))
                assert [result["text"] for result in results] == ["desktop", "nas"]

    run(scenario())


def test_cancelled_hub_call_removes_pending_request(tmp_path):
    async def scenario():
        router = hub_runtime(tmp_path)
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, url):
            async with connect(url) as ws:
                await hello(ws)
                task = asyncio.create_task(router.execute("read", {"path": "."}, "desktop"))
                await ws.recv()
                session = router.registry.get("desktop")
                assert len(session.pending) == 1
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert session.pending == {}

    run(scenario())


def test_malformed_response_unregisters_and_fails_pending(tmp_path):
    async def scenario():
        router = hub_runtime(tmp_path)
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, url):
            async with connect(url) as ws:
                await hello(ws)
                task = asyncio.create_task(router.execute("read", {"path": "."}, "desktop"))
                await ws.recv()
                session = router.registry.get("desktop")
                await ws.send('{"type":"response","protocol_version":99}')
                with pytest.raises(DeviceError, match="offline"):
                    await asyncio.wait_for(task, 1)
                assert session.pending == {}
                await eventually(lambda: not router.registry.sessions)

    run(scenario())


def test_remote_directory_link_cannot_escape_allowed_root(tmp_path):
    import os
    import subprocess
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "linked"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        assert result.returncode == 0
    else:
        link.symlink_to(outside, target_is_directory=True)

    async def scenario():
        router = hub_runtime(tmp_path)
        local = LocalToolDispatcher(WorkspaceManager(root), AppConfig())
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, url):
            async with running_agent(AgentClient(url, "desktop", TOKEN, local)):
                await eventually(lambda: "desktop" in router.registry.sessions)
                for tool, extra in (("read", {}), ("search", {"query": "secret", "mode": "content"})):
                    with pytest.raises(DeviceError, match="outside allowed roots"):
                        await router.execute(tool, {"path": "secret.txt", "workspace": str(link), **extra}, "desktop")
                    with pytest.raises(DeviceError, match="outside workspace"):
                        await router.execute(tool, {"path": "linked/secret.txt", **extra}, "desktop")

    run(scenario())


def test_agent_busy_limit_and_disconnect_cancels_work(tmp_path):
    async def scenario():
        from chat2local.agent.client import MAX_IN_FLIGHT
        router = hub_runtime(tmp_path)
        local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
        started = 0
        cancelled = 0

        async def slow(arguments):
            nonlocal started, cancelled
            started += 1
            try:
                await asyncio.Event().wait()
            finally:
                cancelled += 1

        local.register("slow", slow)
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, url):
            async with running_agent(AgentClient(url, "desktop", TOKEN, local)):
                await eventually(lambda: "desktop" in router.registry.sessions)
                tasks = [asyncio.create_task(router.execute("slow", {}, "desktop")) for _ in range(MAX_IN_FLIGHT)]
                try:
                    await eventually(lambda: started == MAX_IN_FLIGHT)
                    with pytest.raises(DeviceError, match="Agent is busy"):
                        await router.execute("slow", {}, "desktop")
                    await router.registry.get("desktop").websocket.close(code=1001)
                    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)
                    assert all(isinstance(result, DeviceError) and "offline" in str(result) for result in results)
                    await eventually(lambda: cancelled == MAX_IN_FLIGHT)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

    run(scenario())


def test_registry_register_unregister_and_stale_cleanup():
    registry = DeviceRegistry("hub")
    original = DeviceSession("agent", None, ["read"])
    registry.register(original)
    with pytest.raises(DeviceError, match="already connected"):
        registry.register(DeviceSession("agent", None, ["search"]))
    registry.unregister(original)
    replacement = DeviceSession("agent", None, ["search"])
    registry.register(replacement)
    registry.unregister(original)
    assert registry.get("agent") is replacement
    registry.unregister(replacement)
    assert registry.sessions == {}


@pytest.mark.parametrize("model,data", [
    (Request, {"tool": "read", "arguments": {}, "request_id": "bad"}),
    (Request, {"tool": "read", "arguments": {}}),
    (Response, {"request_id": str(uuid4()), "ok": True}),
    (Response, {"request_id": str(uuid4()), "ok": False}),
    (Response, {"request_id": str(uuid4()), "ok": True, "result": {}, "error": "bad"}),
])
def test_protocol_models_reject_invalid_messages(model, data):
    data["protocol_version"] = PROTOCOL_VERSION
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("reason,version,expected", [
    ("Invalid Hub token", 1, "Invalid Hub token"),
    ("Device already connected: desktop", 1, "Device already connected: desktop"),
    ("Invalid hello or unsupported protocol version", 1, "Invalid hello or unsupported protocol version"),
    (f"Invalid Hub token: {TOKEN}\n\x1b[31m" + "x" * 400, 1, "Invalid Hub token: [redacted]"),
    ("unsupported version", 2, "Invalid Hub hello acknowledgement or unsupported protocol version"),
])
def test_agent_logs_registration_reason_safely(tmp_path, caplog, reason, version, expected):
    from fastapi import FastAPI, WebSocket
    application = FastAPI()

    @application.websocket("/device/ws")
    async def reject(ws: WebSocket):
        await ws.accept()
        await ws.receive_text()
        await ws.send_text(json.dumps({
            "type": "hello_ack", "protocol_version": version, "ok": False, "error": reason,
        }))
        await ws.close(code=1008)

    async def scenario():
        local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
        async with running_server(application) as (_, url):
            async with running_agent(AgentClient(url, "desktop", TOKEN, local)) as task:
                await eventually(lambda: any(
                    "Registration rejected:" in record.getMessage() for record in caplog.records
                ))
                assert not task.done()  # rejection still follows the reconnect policy

    caplog.set_level("WARNING", logger="chat2local.agent.client")
    run(scenario())
    messages = [record.getMessage() for record in caplog.records
                if record.name == "chat2local.agent.client"]
    assert any(f"Registration rejected: {expected}" in message for message in messages)
    assert all("reconnecting in 1s" in message for message in messages)
    assert all(TOKEN not in message and "\n" not in message and "\x1b" not in message
               and len(message) < 360 for message in messages)


def test_agent_network_error_log_does_not_echo_credentials(tmp_path, monkeypatch, caplog):
    from chat2local.agent import client as agent_module

    def failed_connect(*args, **kwargs):
        raise OSError(f"connection failed with {TOKEN}")

    async def scenario():
        local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
        async with running_agent(AgentClient("ws://localhost/device/ws", "desktop", TOKEN, local)) as task:
            await eventually(lambda: any("Agent disconnected;" in record.getMessage()
                                         for record in caplog.records))
            assert not task.done()

    monkeypatch.setattr(agent_module, "connect", failed_connect)
    caplog.set_level("WARNING", logger="chat2local.agent.client")
    run(scenario())
    assert "Agent disconnected; reconnecting in 1s" in caplog.text
    assert "Registration rejected:" not in caplog.text
    assert TOKEN not in caplog.text
