"""Supervisor order, real Core cleanup, signals and isolated CLI smoke tests."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import importlib
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest
import uvicorn

from chat2local.agent import client as agent_module
from chat2local.agent.client import AgentClient, RegistrationError
from chat2local.app import create_app
from chat2local.device.registry import DeviceRegistry, DeviceSession
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.hub.router import DeviceRouter
from chat2local.runtime.config import AppConfig
from chat2local.runtime.instance import AlreadyRunningError, InstanceLock, RuntimeDescriptor
from chat2local.runtime.supervisor import CoreServer, RuntimeSupervisor
from chat2local.runtime.workspace import WorkspaceManager
from conftest import run
from test_devices import eventually, running_server
from test_runtime_control import request
from test_runtime_instance import python_script
from test_process_integration import mcp_client


async def published(directory):
    await eventually(lambda: (directory / "runtime.json").exists())
    return json.loads((directory / "runtime.json").read_text(encoding="utf-8"))


def assert_released(directory):
    assert not (directory / "runtime.json").exists()
    lock = InstanceLock(directory)
    lock.acquire()
    lock.release()


def test_supervisor_startup_order_and_normal_exit(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "runtime.json").write_text('{"instance_id":"stale"}')
    supervisor = RuntimeSupervisor("standalone", "device", tmp_path, state=lambda: "running", directory=directory)
    async def core():
        data = json.loads((directory / "runtime.json").read_text())
        assert data["instance_id"] != "stale"
        with pytest.raises(AlreadyRunningError):
            InstanceLock(directory).acquire()
        assert (await request(data["control"]["port"], "ping"))["result"]["instance_id"] == data["instance_id"]
    run(supervisor.run(core, lambda task: task.cancel()))
    assert_released(directory)


def test_actual_uvicorn_bind_failure_preserves_exit_and_cleans_lifespan(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    async def scenario():
        local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
        server = CoreServer(uvicorn.Config(create_app(DeviceRouter("device", local)),
                                          host="127.0.0.1", port=listener.getsockname()[1], log_level="critical"))
        supervisor = RuntimeSupervisor("standalone", "device", tmp_path, state=lambda: "starting", directory=tmp_path / "data")
        with pytest.raises(SystemExit) as failure:
            await supervisor.run(server.serve, lambda task: setattr(server, "should_exit", True))
        assert failure.value.code == 3
        assert local.process_manager._closing
        assert_released(supervisor.directory)
    try:
        run(scenario())
    finally:
        listener.close()


def test_unexpected_listener_startup_failure_cleans_lifespan_before_unlock(tmp_path, monkeypatch):
    async def scenario():
        local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
        directory = tmp_path / "data"
        shutdowns = []
        original_shutdown = local.process_manager.shutdown
        async def shutdown():
            with pytest.raises(AlreadyRunningError):
                InstanceLock(directory).acquire()
            assert (directory / "runtime.json").exists()
            shutdowns.append(True)
            await original_shutdown()
        monkeypatch.setattr(local.process_manager, "shutdown", shutdown)
        server = CoreServer(uvicorn.Config(create_app(DeviceRouter("device", local)),
                                          host="127.0.0.1", port=65536, log_level="critical"))
        supervisor = RuntimeSupervisor("standalone", "device", tmp_path, state=lambda: "starting", directory=directory)
        with pytest.raises(OverflowError):
            await supervisor.run(server.serve, lambda task: setattr(server, "should_exit", True))
        assert shutdowns == [True]
        assert_released(directory)
    run(scenario())


def test_single_core_keeps_tool_workspace_selection_and_boundary(tmp_path):
    allowed = tmp_path / "allowed"
    a, b, outside = allowed / "A", allowed / "B", tmp_path / "outside"
    for directory in (a, b, outside):
        directory.mkdir(parents=True)
        (directory / "file.txt").write_text(directory.name)
    async def scenario():
        local = LocalToolDispatcher(WorkspaceManager(a, allowed_roots=[allowed]), AppConfig())
        server = CoreServer(uvicorn.Config(create_app(DeviceRouter("device", local)),
                                          host="127.0.0.1", port=0, log_level="critical", access_log=False))
        supervisor = RuntimeSupervisor("standalone", "device", local.workspace.root,
                                       state=lambda: "running", directory=tmp_path / "data")
        task = asyncio.create_task(supervisor.run(server.serve, lambda task: setattr(server, "should_exit", True)))
        try:
            data = await published(supervisor.directory)
            await eventually(lambda: server.started)
            url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
            async with mcp_client(url) as client:
                tools = await client.list_tools()
                assert len(tools.tools) == 10
                first = await client.call_tool("read", {"path": "file.txt"})
                second = await client.call_tool("read", {"path": "file.txt", "workspace": str(b.resolve())})
                denied = await client.call_tool("read", {"path": "file.txt", "workspace": str(outside.resolve())})
                assert json.loads(first.content[0].text)["text"] == "A"
                assert json.loads(second.content[0].text)["text"] == "B"
                assert denied.is_error
            assert (await request(data["control"]["port"], "status"))["result"]["workspace"] == str(a.resolve())
        finally:
            supervisor.request_stop()
            await asyncio.wait_for(task, 5)
        assert_released(supervisor.directory)
    run(scenario())


@pytest.mark.parametrize("stage", ["control", "publish", "core", "system_exit"])
def test_startup_failure_cleans_ipc_descriptor_lock(tmp_path, monkeypatch, stage):
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "runtime.json").write_text('{"instance_id":"stale"}')
    supervisor = RuntimeSupervisor("hub", "device", tmp_path, state=lambda: "running", directory=directory)
    failure = SystemExit(3) if stage == "system_exit" else RuntimeError("original failure")
    async def core():
        if stage in ("core", "system_exit"):
            raise failure
        pytest.fail("Core must not start on control/publish failure")
    if stage == "control":
        async def fail_start():
            raise failure
        monkeypatch.setattr(supervisor.control, "start", fail_start)
    if stage == "publish":
        def fail_publish(self, directory):
            raise failure
        monkeypatch.setattr(RuntimeDescriptor, "publish", fail_publish)
    with pytest.raises(type(failure)) as caught:
        run(supervisor.run(core, lambda task: task.cancel()))
    assert caught.value is failure
    if stage in ("control", "publish"):
        assert json.loads((directory / "runtime.json").read_text())["instance_id"] == "stale"
        (directory / "runtime.json").unlink()
    assert supervisor.control._server is None or not supervisor.control._server.is_serving()
    assert_released(directory)


def test_second_core_does_not_touch_shared_descriptor(tmp_path):
    lock = InstanceLock(tmp_path)
    lock.acquire()
    (tmp_path / "runtime.json").write_text("existing descriptor")
    supervisor = RuntimeSupervisor("agent", "device", tmp_path, state=lambda: "offline", directory=tmp_path)
    async def core():
        pytest.fail("second Core started")
    try:
        with pytest.raises(AlreadyRunningError):
            run(supervisor.run(core, lambda task: task.cancel()))
        assert (tmp_path / "runtime.json").read_text() == "existing descriptor"
        assert supervisor.control._server is None
    finally:
        lock.release()


def test_cleanup_preserves_other_instance_descriptor(tmp_path):
    supervisor = RuntimeSupervisor("hub", "device", tmp_path, state=lambda: "running", directory=tmp_path)
    async def core():
        replace(supervisor.descriptor, instance_id="other").publish(tmp_path)
    run(supervisor.run(core, lambda task: task.cancel()))
    assert json.loads((tmp_path / "runtime.json").read_text())["instance_id"] == "other"


def test_stop_idempotence_and_repeated_cancellation_wait_for_core_cleanup(tmp_path):
    async def scenario():
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        stop_calls = []
        supervisor = RuntimeSupervisor("agent", "device", tmp_path, state=lambda: "connected", directory=tmp_path)
        async def core():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
        def stop(task):
            stop_calls.append(True)
            task.cancel()
        owner = asyncio.create_task(supervisor.run(core, stop))
        await entered.wait()
        data = await published(tmp_path)
        port = data["control"]["port"]
        assert (await request(port, "status"))["result"]["state"] == "connected"
        assert (await request(port, "stop"))["result"]["accepted"]
        await cleaning.wait()
        assert (await request(port, "stop"))["result"]["accepted"]
        assert (await request(port, "status"))["result"]["state"] == "stopping"
        owner.cancel()
        await asyncio.sleep(0)
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done() and (tmp_path / "runtime.json").exists()
        with pytest.raises(AlreadyRunningError):
            InstanceLock(tmp_path).acquire()
        release.set()
        await asyncio.wait_for(owner, 5)
        assert stop_calls == [True]
        assert_released(tmp_path)
    run(scenario())


def test_outer_cancellation_still_waits_and_propagates(tmp_path):
    async def scenario():
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        supervisor = RuntimeSupervisor("agent", "device", tmp_path, state=lambda: "connecting", directory=tmp_path)
        async def core():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
        owner = asyncio.create_task(supervisor.run(core, lambda task: task.cancel()))
        await entered.wait()
        owner.cancel()
        await cleaning.wait()
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(owner, 5)
        assert_released(tmp_path)
    run(scenario())


@pytest.mark.parametrize("mode", ["standalone", "hub"])
@pytest.mark.parametrize("failure", [False, True])
def test_http_core_lifecycle_and_existing_lifespan_cleanup(tmp_path, monkeypatch, mode, failure):
    async def scenario():
        local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
        registry = DeviceRegistry("device") if mode == "hub" else None
        router = DeviceRouter("device", local, registry)
        app = create_app(router, hub_token="test-token" if registry else None)
        cleanups = []
        original_shutdown = local.process_manager.shutdown
        async def shutdown():
            cleanups.append("process")
            await original_shutdown()
        monkeypatch.setattr(local.process_manager, "shutdown", shutdown)
        if registry:
            original_close = registry.close
            async def close():
                cleanups.append("registry")
                await original_close()
            monkeypatch.setattr(registry, "close", close)
            class Socket:
                async def close(self, code):
                    cleanups.append("websocket")
            registry.register(DeviceSession("remote", Socket(), ["read"]))
        server = CoreServer(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical",
                                          access_log=False, timeout_graceful_shutdown=1))
        supervisor = RuntimeSupervisor(mode, "device", local.workspace.root,
                                       state=lambda: "running" if server.started else "starting", directory=tmp_path / "data")
        fail = asyncio.Event()
        if failure:
            async def broken_loop():
                await fail.wait()
                raise RuntimeError("server failed")
            monkeypatch.setattr(server, "main_loop", broken_loop)
        task = asyncio.create_task(supervisor.run(server.serve, lambda task: setattr(server, "should_exit", True)))
        try:
            data = await published(supervisor.directory)
            await eventually(lambda: server.started)
            port = data["control"]["port"]
            status = (await request(port, "status"))["result"]
            assert status["mode"] == mode and status["state"] == "running"
            assert status["workspace"] == str(tmp_path.resolve())
            assert set(status) == {"instance_id", "pid", "mode", "device_id", "workspace", "started_at", "version", "state"}
            if failure:
                fail.set()
                with pytest.raises(RuntimeError, match="server failed"):
                    await asyncio.wait_for(task, 5)
            else:
                assert (await request(port, "ping"))["result"]["instance_id"] == data["instance_id"]
                assert (await request(port, "stop"))["ok"]
                await asyncio.wait_for(task, 5)
            assert cleanups == (["registry", "websocket", "process"] if registry else ["process"])
            assert_released(supervisor.directory)
            assert not supervisor.control._server.is_serving()
        finally:
            if not task.done():
                fail.set()
                supervisor.request_stop()
                await asyncio.wait_for(task, 5)
    run(scenario())


def test_agent_reconnect_sleep_stop_uses_existing_finally(tmp_path, monkeypatch):
    async def scenario():
        local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
        client = AgentClient("ws://127.0.0.1:1/device/ws", "agent", "secret", local, proxy="direct")
        sleeping = asyncio.Event()
        @asynccontextmanager
        async def failed_connect(*args, **kwargs):
            raise OSError("failed")
            yield
        original_sleep = asyncio.sleep
        async def sleep(delay):
            if delay == 1:
                sleeping.set()
                await original_sleep(30)
            else:
                await original_sleep(delay)
        monkeypatch.setattr(agent_module, "connect", failed_connect)
        monkeypatch.setattr(agent_module.asyncio, "sleep", sleep)
        shutdowns = []
        original_shutdown = local.process_manager.shutdown
        async def shutdown():
            shutdowns.append(True)
            await original_shutdown()
        monkeypatch.setattr(local.process_manager, "shutdown", shutdown)
        supervisor = RuntimeSupervisor("agent", "agent", tmp_path, state=lambda: client.state, directory=tmp_path / "data")
        task = asyncio.create_task(supervisor.run(client.run, lambda task: task.cancel()))
        await asyncio.wait_for(sleeping.wait(), 5)
        data = await published(supervisor.directory)
        assert (await request(data["control"]["port"], "status"))["result"]["state"] == "reconnecting"
        await request(data["control"]["port"], "stop")
        await asyncio.wait_for(task, 2)
        assert client.state == "offline" and shutdowns == [True]
        assert_released(supervisor.directory)
    run(scenario())


def test_agent_connected_stop_closes_websocket_and_process_runtime(tmp_path, monkeypatch):
    async def scenario():
        hub = DeviceRouter("hub", LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig()), DeviceRegistry("hub"))
        async with running_server(create_app(hub, hub_token="test-token")) as (_, url):
            client = AgentClient(url, "agent", "test-token", LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig()), proxy="direct")
            supervisor = RuntimeSupervisor("agent", "agent", tmp_path, state=lambda: client.state, directory=tmp_path / "data")
            shutdowns = []
            original_shutdown = client.local.process_manager.shutdown
            async def shutdown():
                shutdowns.append(True)
                await original_shutdown()
            monkeypatch.setattr(client.local.process_manager, "shutdown", shutdown)
            task = asyncio.create_task(supervisor.run(client.run, lambda task: task.cancel()))
            try:
                await eventually(lambda: client.state == "connected")
                data = await published(supervisor.directory)
                port = data["control"]["port"]
                assert (await request(port, "status"))["result"]["state"] == "connected"
                await request(port, "stop")
                await asyncio.wait_for(task, 5)
                await eventually(lambda: "agent" not in hub.registry.sessions)
                assert client.state == "offline" and shutdowns == [True]
                assert_released(supervisor.directory)
            finally:
                if not task.done():
                    supervisor.request_stop()
                    await task
    run(scenario())


def test_agent_fatal_error_preserves_exception_and_cleanup(tmp_path, monkeypatch):
    client = AgentClient("ws://localhost/device/ws", "agent", "secret", LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig()))
    failure = RegistrationError("authentication_failed")
    async def fail():
        raise failure
    monkeypatch.setattr(client, "_run", fail)
    supervisor = RuntimeSupervisor("agent", "agent", tmp_path, state=lambda: client.state, directory=tmp_path / "data")
    with pytest.raises(RegistrationError) as caught:
        run(supervisor.run(client.run, lambda task: task.cancel()))
    assert caught.value is failure and client.state == "offline"
    assert client.local.process_manager._closing
    assert_released(supervisor.directory)


def test_server_shutdown_failure_preserves_original_runtime_error(tmp_path, monkeypatch, caplog):
    main_error, shutdown_error = RuntimeError("server failed"), RuntimeError("cleanup failed")
    server = CoreServer(uvicorn.Config(create_app(DeviceRouter(
        "device", LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig()),
    ))))
    async def serve(self, sockets=None):
        self.started = True
        raise main_error
    async def shutdown(sockets=None):
        raise shutdown_error
    monkeypatch.setattr(uvicorn.Server, "serve", serve)
    monkeypatch.setattr(server, "shutdown", shutdown)
    supervisor = RuntimeSupervisor("standalone", "device", tmp_path, state=lambda: "running", directory=tmp_path / "data")
    with pytest.raises(RuntimeError) as failure:
        run(supervisor.run(server.serve, lambda task: setattr(server, "should_exit", True)))
    assert failure.value is main_error
    assert "Unexpected server shutdown failure" in caplog.text
    assert_released(supervisor.directory)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM] + ([signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else []))
def test_signals_request_unified_stop_and_restore_handlers(tmp_path, sig):
    supervisor = RuntimeSupervisor("standalone", "device", tmp_path, state=lambda: "running", directory=tmp_path)
    original = signal.getsignal(sig)
    async def core():
        handler = signal.getsignal(sig)
        assert callable(handler) and handler != original
        handler(sig, None)
        await asyncio.Event().wait()
    run(supervisor.run(core, lambda task: task.cancel()))
    assert signal.getsignal(sig) == original
    assert_released(tmp_path)


BOOTSTRAP = """
import importlib, sys
from pathlib import Path
from chat2local.runtime import config
data, workspace, mode = sys.argv[1:]
config.user_data_directory = lambda: Path(data)
config.DEFAULT_CONFIG_PATH = Path(data) / 'config.yaml'
sys.argv = ['chat2local', '--workspace', workspace, '--device-id', 'smoke', '--port', '0']
if mode != 'standalone':
    sys.argv += [mode, '--token', 'SECRET-SMOKE-TOKEN']
if mode == 'agent':
    sys.argv += ['--hub-url', 'ws://127.0.0.1:1/device/ws']
importlib.import_module('chat2local.main').main()
"""


def spawn_core(directory, workspace, mode):
    return subprocess.Popen([*python_script(BOOTSTRAP), str(directory), str(workspace), mode],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}))


def wait_descriptor(child, directory, previous_id=None):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if child.poll() is not None:
            pytest.fail(child.communicate()[1].decode(errors="replace"))
        path = directory / "runtime.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["instance_id"] != previous_id:
                return data
        time.sleep(0.02)
    pytest.fail("Core failed to publish descriptor")


def test_killed_core_leaves_stale_descriptor_next_core_replaces_it(tmp_path):
    directory = tmp_path / "data"
    child = spawn_core(directory, tmp_path, "standalone")
    try:
        stale = wait_descriptor(child, directory)
        child.kill()
        child.communicate(timeout=10)
        assert json.loads((directory / "runtime.json").read_text())["instance_id"] == stale["instance_id"]
        child = spawn_core(directory, tmp_path, "standalone")
        current = wait_descriptor(child, directory, previous_id=stale["instance_id"])
        async def stop():
            port = current["control"]["port"]
            assert (await request(port, "ping"))["result"]["instance_id"] == current["instance_id"]
            await request(port, "stop")
        run(stop())
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, err.decode(errors="replace")
        assert_released(directory)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def test_agent_connecting_stop_and_finally(tmp_path, monkeypatch):
    async def scenario():
        connecting = asyncio.Event()
        @asynccontextmanager
        async def connect(*args, **kwargs):
            connecting.set()
            await asyncio.Event().wait()
            yield
        monkeypatch.setattr(agent_module, "connect", connect)
        client = AgentClient("ws://localhost/device/ws", "agent", "secret", LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig()))
        supervisor = RuntimeSupervisor("agent", "agent", tmp_path, state=lambda: client.state, directory=tmp_path / "data")
        task = asyncio.create_task(supervisor.run(client.run, lambda task: task.cancel()))
        await asyncio.wait_for(connecting.wait(), 5)
        data = await published(supervisor.directory)
        assert (await request(data["control"]["port"], "status"))["result"]["state"] == "connecting"
        await request(data["control"]["port"], "stop")
        await asyncio.wait_for(task, 5)
        assert client.state == "offline" and client.local.process_manager._closing
        assert_released(supervisor.directory)
    run(scenario())


def test_runtime_management_cleanup_failure_preserves_core_error(tmp_path, monkeypatch, caplog):
    supervisor = RuntimeSupervisor("hub", "device", tmp_path, state=lambda: "running", directory=tmp_path)
    failure = RuntimeError("Core failed")
    async def core():
        raise failure
    original_close = supervisor.control.close
    async def close():
        await original_close()
        raise OSError("control cleanup failed")
    monkeypatch.setattr(supervisor.control, "close", close)
    with pytest.raises(RuntimeError) as caught:
        run(supervisor.run(core, lambda task: task.cancel()))
    assert caught.value is failure
    assert "Unexpected runtime management cleanup failure" in caplog.text
    assert_released(tmp_path)


@pytest.mark.parametrize("mode", ["standalone", "hub", "agent"])
def test_real_cli_smoke_ping_status_stop_restart_and_cross_mode_lock(tmp_path, mode):
    directory = tmp_path / "user-data"
    directory.mkdir()
    (directory / "config.yaml").write_text("agent:\n  proxy: direct\n")
    child = None
    ids = []
    try:
        for iteration in range(2):
            child = spawn_core(directory, tmp_path, mode)
            data = wait_descriptor(child, directory)
            ids.append(data["instance_id"])
            assert data["pid"] == child.pid and data["mode"] == mode
            assert data["workspace"] == str(tmp_path.resolve())
            if iteration == 0:
                second_mode = "agent" if mode != "agent" else "hub"
                second = spawn_core(directory, tmp_path, second_mode)
                try:
                    out, err = second.communicate(timeout=10)
                finally:
                    if second.poll() is None:
                        second.kill()
                        second.communicate(timeout=10)
                assert second.returncode == 1 and b"Chat2Local is already running" in err
                assert json.loads((directory / "runtime.json").read_text())["instance_id"] == ids[-1]
            async def checks():
                port = data["control"]["port"]
                assert (await request(port, "ping"))["result"]["instance_id"] == ids[-1]
                status = (await request(port, "status"))["result"]
                assert status["mode"] == mode and status["pid"] == child.pid
                assert "SECRET-SMOKE-TOKEN" not in json.dumps(status)
                assert (await request(port, "stop"))["result"]["accepted"]
            run(checks())
            out, err = child.communicate(timeout=10)
            assert child.returncode == 0, err.decode(errors="replace")
            assert b"SECRET-SMOKE-TOKEN" not in err and b"Traceback" not in err
            assert_released(directory)
        assert ids[0] != ids[1]
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


@pytest.mark.parametrize("mode", ["standalone", "hub", "agent"])
def test_cli_reports_lock_failure_cleanly(tmp_path, monkeypatch, capsys, mode):
    entry = importlib.import_module("chat2local.main")
    monkeypatch.setattr(entry, "configure_logging", lambda **kwargs: None)
    from chat2local.runtime import config
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "absent.yaml")
    lock = InstanceLock(config.user_data_directory())
    lock.acquire()
    argv = ["chat2local", "--workspace", str(tmp_path)]
    if mode != "standalone":
        argv += [mode, "--token", "secret"]
    if mode == "agent":
        argv += ["--hub-url", "ws://localhost/device/ws"]
    monkeypatch.setattr(sys, "argv", argv)
    try:
        with pytest.raises(SystemExit) as failure:
            entry.main()
        assert failure.value.code == 1
        assert "Chat2Local is already running" in capsys.readouterr().err
    finally:
        lock.release()
