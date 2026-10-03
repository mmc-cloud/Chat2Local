"""Real HTTP MCP and Hub/Agent WebSocket process routing and shutdown."""

import asyncio
import base64
from contextlib import asynccontextmanager
from pathlib import Path
import shlex
import sys

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from chat2local.agent.client import AgentClient
from chat2local.app import create_app
from chat2local.device.registry import DeviceRegistry
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.hub.router import DeviceRouter
from chat2local.mcp import tools as mcp_tools
from chat2local.runtime.config import AppConfig, ProcessConfig
from chat2local.runtime.process_manager import ProcessManager
from chat2local.runtime.workspace import WorkspaceManager
from conftest import run
from test_devices import TOKEN, decode, eventually, running_agent, running_server
from test_process_manager import pid_alive
from test_process_tools import LOCAL_TOOLS, OUTPUT_FIELDS


def device_runtime(root, *, allowed_roots=None, foreground_timeout=0.08):
    config = AppConfig(process=ProcessConfig(
        foreground_timeout=foreground_timeout, terminate_grace_period=0.05,
    ))
    manager = ProcessManager(config.process)  # Real host ShellResolver, no shell mock.
    return LocalToolDispatcher(WorkspaceManager(root, allowed_roots=allowed_roots), config, manager)


def python_command(local, code):
    # Pass source through a base64 literal to avoid platform quoting influencing
    # lifecycle assertions; the configured shell still executes this full command.
    source = base64.b64encode(code.encode()).decode()
    program = f"import base64;exec(base64.b64decode('{source}'))"
    shell = local.process_manager.shell.kind
    if shell in ("pwsh", "powershell"):
        executable = "& '" + sys.executable.replace("'", "''") + "'"
        return f'{executable} -u -X utf8 -c "{program}"'
    if shell == "cmd":
        return f'"{sys.executable}" -u -X utf8 -c "{program}"'
    return f"{shlex.quote(sys.executable)} -u -X utf8 -c {shlex.quote(program)}"


@asynccontextmanager
async def mcp_client(url):
    async with streamable_http_client(url + "/mcp/") as streams:
        async with ClientSession(streams[0], streams[1]) as client:
            await client.initialize()
            yield client


@pytest.mark.parametrize("remote", [False, True])
def test_real_mcp_process_flow_and_device_ownership(tmp_path, remote):
    hub, a, b = (tmp_path / name for name in ("hub", "A", "B"))
    for root in (hub, a, b):
        root.mkdir()
        (root / "marker").write_text(root.name, encoding="utf-8")

    async def scenario():
        hub_local = device_runtime(hub)
        agent_local = device_runtime(a, allowed_roots=[tmp_path]) if remote else None
        router = DeviceRouter("hub", hub_local, DeviceRegistry("hub") if remote else None)
        target = agent_local if remote else hub_local
        device = "desktop" if remote else None
        async with running_server(create_app(router, hub_token=TOKEN if remote else None)) as (url, ws_url):
            @asynccontextmanager
            async def connected():
                if remote:
                    async with running_agent(AgentClient(ws_url, "desktop", TOKEN, agent_local)):
                        await eventually(lambda: "desktop" in router.registry.sessions)
                        yield
                else:
                    yield
            async with connected(), mcp_client(url) as client:
                schemas = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}
                assert set(schemas) == set(LOCAL_TOOLS) | {"list_devices"}
                for name, fields, required in (
                    ("exec_command", ["command", "cwd", "workspace", "device"], ["command"]),
                    ("interact_process", ["process_id", "input", "device"], ["process_id"]),
                    ("kill_process", ["process_id", "device"], ["process_id"]),
                ):
                    assert list(schemas[name]["properties"]) == fields
                    assert schemas[name]["required"] == required
                devices = decode(await client.call_tool("list_devices", {}))["devices"]
                assert all(entry["tools"] == list(LOCAL_TOOLS) for entry in devices)
                assert all("list_devices" not in entry["tools"] for entry in devices)

                command = python_command(target, "import sys; print('ready'); "
                                         "print('error',file=sys.stderr); sys.stdin.buffer.read(1); "
                                         "print('reply'); sys.stdin.buffer.read(1)")
                first = decode(await client.call_tool("exec_command", {"command": command, "device": device}))
                pid = first["process_id"]
                record = target.process_manager.records[pid]
                assert first["state"] == "running" and set(first) == OUTPUT_FIELDS
                await eventually(lambda: record.stdout.retained_end > 0 and record.stderr.retained_end > 0)
                second = decode(await client.call_tool("interact_process", {"process_id": pid, "device": device}))
                assert (first["stdout"] + second["stdout"]).strip() == "ready"
                assert (first["stderr"] + second["stderr"]).strip() == "error"
                if remote:
                    assert pid not in hub_local.process_manager.records
                    for tool in ("interact_process", "kill_process"):
                        wrong = await client.call_tool(tool, {"process_id": pid, "device": "hub"})
                        assert wrong.is_error and "unknown_process" in wrong.content[0].text
                    hub_process = decode(await client.call_tool("exec_command", {
                        "command": python_command(hub_local, "import time; time.sleep(60)"), "device": "hub",
                    }))
                    assert hub_process["process_id"] not in target.process_manager.records
                    wrong = await client.call_tool("interact_process", {
                        "process_id": hub_process["process_id"], "device": "desktop",
                    })
                    assert wrong.is_error and "unknown_process" in wrong.content[0].text
                    await client.call_tool("kill_process", {"process_id": hub_process["process_id"], "device": "hub"})
                response = decode(await client.call_tool("interact_process", {
                    "process_id": pid, "input": "x", "device": device,
                }))
                await eventually(lambda: record.stdout.retained_end >= len("ready\nreply\n"))
                tail = decode(await client.call_tool("interact_process", {"process_id": pid, "device": device}))
                assert (response["stdout"] + tail["stdout"]).strip() == "reply"
                killed = decode(await client.call_tool("kill_process", {"process_id": pid, "device": device}))
                assert killed["outcome"] == "terminated" and killed["state"] == "terminated"
                assert not killed["draining"] and pid in target.process_manager.records
                final = decode(await client.call_tool("interact_process", {"process_id": pid, "device": device}))
                assert final["state"] == "terminated" and final["stdout"] == final["stderr"] == ""

                # Remote paths are resolved only by the target device. Its default
                # stays A after B is selected; cwd cannot cross to sibling A.
                if remote:
                    selected = decode(await client.call_tool("exec_command", {
                        "command": python_command(
                            target, "from pathlib import Path; print(Path('marker').read_text())",
                        ),
                        "workspace": str(b), "device": device,
                    }))
                    await target.process_manager.wait(selected["process_id"], timeout=5)
                    extra = decode(await client.call_tool("interact_process", {
                        "process_id": selected["process_id"], "device": device,
                    }))
                    assert (selected["stdout"] + extra["stdout"]).strip() == "B"
                    assert target.workspace.root == a.resolve()
                    denied = await client.call_tool("exec_command", {
                        "command": command, "workspace": str(b), "cwd": "../A", "device": device,
                    })
                    assert denied.is_error and "outside workspace" in denied.content[0].text
        assert hub_local.process_manager.records == {}
        if remote:
            assert target.process_manager.records == {}
    run(scenario())


@pytest.mark.parametrize("owner", ["standalone", "hub", "agent"])
@pytest.mark.parametrize("kill", [False, True])
def test_kill_and_shutdown_clean_owned_process_tree(tmp_path, owner, kill):
    hub, agent_root = tmp_path / "hub", tmp_path / "agent"
    hub.mkdir()
    agent_root.mkdir()
    async def scenario():
        hub_local = device_runtime(hub)
        remote = owner == "agent"
        local = device_runtime(agent_root) if remote else hub_local
        registry = DeviceRegistry("hub") if owner != "standalone" else None
        router = DeviceRouter("hub", hub_local, registry)
        # Exercise default-module app's lazy router ownership for standalone too.
        if owner == "standalone":
            mcp_tools.configure_router(router)
            app = create_app()
        else:
            app = create_app(router, hub_token=TOKEN)
        child_pid = None
        record = None
        async with running_server(app) as (url, ws_url):
            @asynccontextmanager
            async def connected():
                if remote:
                    async with running_agent(AgentClient(ws_url, "desktop", TOKEN, local)):
                        await eventually(lambda: "desktop" in registry.sessions)
                        yield
                else:
                    yield
            async with connected(), mcp_client(url) as client:
                code = ("import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-u','-c',"
                        "\"import time; print('child final'); time.sleep(60)\"]); "
                        "print('CHILD='+str(child.pid)); time.sleep(60)")
                result = decode(await client.call_tool("exec_command", {
                    "command": python_command(local, code), "device": "desktop" if remote else None,
                }))
                record = local.process_manager.records[result["process_id"]]
                output = result["stdout"]
                async with asyncio.timeout(5):
                    while "CHILD=" not in output or "\n" not in output.split("CHILD=", 1)[-1]:
                        output += decode(await client.call_tool("interact_process", {
                            "process_id": result["process_id"], "device": "desktop" if remote else None,
                        }))["stdout"]
                        await asyncio.sleep(0.01)
                child_pid = int(output.split("CHILD=", 1)[1].splitlines()[0])
                assert pid_alive(child_pid) and record.process.returncode is None
                if kill:
                    killed = decode(await client.call_tool("kill_process", {
                        "process_id": result["process_id"], "device": "desktop" if remote else None,
                    }))
                    assert killed["outcome"] == killed["state"] == "terminated"
                    await eventually(lambda: not pid_alive(child_pid))
                    assert result["process_id"] in local.process_manager.records
                    retained = decode(await client.call_tool("interact_process", {
                        "process_id": result["process_id"], "device": "desktop" if remote else None,
                    }))
                    assert retained["state"] == "terminated" and not retained["draining"]
            if remote:
                await eventually(lambda: not pid_alive(child_pid))
                assert local.process_manager.records == {}
        await eventually(lambda: not pid_alive(child_pid))
        assert record.finished.is_set() and record.process.returncode is not None
        assert all(task.done() for task in record.drain_tasks)
        assert hub_local.process_manager.records == {}
    run(scenario())


@pytest.mark.parametrize("timeout", [False, True])
def test_agent_reconnect_preserves_registry_and_never_replays_exec(tmp_path, timeout, monkeypatch):
    hub, root = tmp_path / "hub", tmp_path / "agent"
    hub.mkdir()
    root.mkdir()
    async def scenario():
        hub_local = device_runtime(hub)
        local = device_runtime(root, foreground_timeout=0.5 if timeout else 0.08)
        manager = local.process_manager
        registry = DeviceRegistry("hub")
        router = DeviceRouter("hub", hub_local, registry, timeout=0.06 if timeout else 30)
        commands = []
        shutdowns = []
        original_execute, original_shutdown = local.execute, manager.shutdown
        async def execute(tool, arguments):
            if tool == "exec_command":
                commands.append(arguments)
            return await original_execute(tool, arguments)
        async def shutdown():
            shutdowns.append(True)
            await original_shutdown()
        monkeypatch.setattr(local, "execute", execute)
        monkeypatch.setattr(manager, "shutdown", shutdown)
        async with running_server(create_app(router, hub_token=TOKEN)) as (url, ws_url):
            async with running_agent(AgentClient(ws_url, "desktop", TOKEN, local)), mcp_client(url) as client:
                await eventually(lambda: "desktop" in registry.sessions)
                session = registry.get("desktop")
                code = ("from pathlib import Path; import sys; "
                        "Path('executions').open('a').write('once\\n'); print('ready'); "
                        "sys.stdin.buffer.read(1); print('after reconnect'); sys.stdin.buffer.read(1)")
                result = await client.call_tool("exec_command", {
                    "command": python_command(local, code), "device": "desktop",
                })
                if timeout:
                    assert result.is_error and "timed out" in result.content[0].text
                    await eventually(lambda: len(manager.records) == 1)
                    pid = next(iter(manager.records))
                else:
                    pid = decode(result)["process_id"]
                record = manager.records[pid]
                await eventually(lambda: (root / "executions").exists() and record.stdout.retained_end > 0)
                await session.websocket.close(code=1001)
                await eventually(lambda: registry.sessions.get("desktop") is not None
                                 and registry.sessions["desktop"] is not session)
                assert local.process_manager is manager and manager.records[pid] is record
                assert record.process.returncode is None and shutdowns == [] and len(commands) == 1
                assert (root / "executions").read_text() == "once\n"
                router.timeout = 30
                resumed = decode(await client.call_tool("interact_process", {
                    "process_id": pid, "input": "x", "device": "desktop",
                }))
                assert "after reconnect" in resumed["stdout"]
                killed = decode(await client.call_tool("kill_process", {"process_id": pid, "device": "desktop"}))
                assert killed["state"] == "terminated" and killed["outcome"] == "terminated"
                assert len(commands) == 1 and (root / "executions").read_text() == "once\n"
                assert shutdowns == []
            assert shutdowns == [True] and manager.records == {}
    run(scenario())


def test_hub_shutdown_does_not_shutdown_remote_agent_manager(tmp_path):
    hub, root = tmp_path / "hub", tmp_path / "agent"
    hub.mkdir()
    root.mkdir()
    async def scenario():
        hub_local, local = device_runtime(hub), device_runtime(root)
        router = DeviceRouter("hub", hub_local, DeviceRegistry("hub"))
        server_context = running_server(create_app(router, hub_token=TOKEN))
        url, ws_url = await server_context.__aenter__()
        server_closed = False
        try:
            async with running_agent(AgentClient(ws_url, "desktop", TOKEN, local)):
                await eventually(lambda: "desktop" in router.registry.sessions)
                async with mcp_client(url) as client:
                    remote = decode(await client.call_tool("exec_command", {
                        "command": python_command(local, "print('remote'); import time; time.sleep(60)"),
                        "device": "desktop",
                    }))
                    own = decode(await client.call_tool("exec_command", {
                        "command": python_command(hub_local, "import time; time.sleep(60)"),
                    }))
                remote_record = local.process_manager.records[remote["process_id"]]
                own_record = hub_local.process_manager.records[own["process_id"]]
                await server_context.__aexit__(None, None, None)
                server_closed = True
                assert hub_local.process_manager.records == {} and own_record.finished.is_set()
                assert remote_record.process.returncode is None
                assert local.process_manager.status(remote["process_id"]).state == "running"
            assert remote_record.finished.is_set() and local.process_manager.records == {}
        finally:
            if not server_closed:
                await server_context.__aexit__(None, None, None)
    run(scenario())


def test_registry_close_failure_still_shuts_down_hub_processes(tmp_path, monkeypatch):
    async def scenario():
        local = device_runtime(tmp_path)
        router = DeviceRouter("hub", local, DeviceRegistry("hub"))
        app = create_app(router, hub_token=TOKEN)
        async def fail():
            raise RuntimeError("Registry close failed")
        monkeypatch.setattr(router.registry, "close", fail)
        with pytest.RaisesGroup(pytest.RaisesExc(RuntimeError, match="Registry close failed")):
            async with app.router.lifespan_context(app):
                result = await local.execute("exec_command", {
                    "command": python_command(local, "import time; time.sleep(60)"),
                })
                record = local.process_manager.records[result["process_id"]]
        assert record.finished.is_set() and local.process_manager.records == {}
    run(scenario())


def test_agent_run_failure_still_shuts_down_local_processes(tmp_path, monkeypatch):
    async def scenario():
        local = device_runtime(tmp_path)
        agent = AgentClient("ws://127.0.0.1:1/device/ws", "desktop", TOKEN, local)
        async def failed_run():
            await local.execute("exec_command", {
                "command": python_command(local, "import time; time.sleep(60)"),
            })
            raise RuntimeError("Agent loop failed")
        monkeypatch.setattr(agent, "_run", failed_run)
        with pytest.raises(RuntimeError, match="Agent loop failed"):
            await agent.run()
        assert local.process_manager.records == {}
    run(scenario())
