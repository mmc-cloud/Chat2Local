"""Read-only device snapshots use real router providers over local control."""

from importlib import import_module
from types import SimpleNamespace

import pytest

main = import_module("chat2local.main")
from conftest import run
from test_management_runtime import serve
from test_runtime_control import exchange, request

from chat2local.device.registry import DeviceRegistry, DeviceSession
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.hub.router import DeviceRouter
from chat2local.management.runtime import RuntimeClient
from chat2local.runtime.config import AppConfig
from chat2local.runtime.control import MAX_RESPONSE_BYTES, ControlServer


@pytest.mark.parametrize("mode", ["standalone", "hub", "agent"])
def test_entrypoint_providers_and_devices_ipc(tmp_path, workspace, monkeypatch, mode):
    local = LocalToolDispatcher(workspace, AppConfig())
    registry = DeviceRegistry("local") if mode == "hub" else None
    router = DeviceRouter("local", local, registry)
    if registry:
        registry.register(
            DeviceSession("remote", SimpleNamespace(), ["read", "search"])
        )
    captured = {}

    class Supervisor:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def run(self, *args):
            pass

    monkeypatch.setattr(main, "RuntimeSupervisor", Supervisor)
    if mode == "agent":
        from chat2local.agent.client import AgentClient

        client = AgentClient("ws://127.0.0.1:1/device/ws", "local", "secret", local)
        run(main._run_agent(client))
    else:
        run(
            main._run_server(
                SimpleNamespace(state=SimpleNamespace(router=router)),
                mode=mode,
                device_id="local",
                workspace=workspace.root,
                host="127.0.0.1",
                port=0,
            )
        )
    provider = captured["devices"]
    snapshot = provider()
    assert [device["device_id"] for device in snapshot["devices"]] == (
        ["local", "remote"] if mode == "hub" else ["local"]
    )
    assert all(device["online"] for device in snapshot["devices"])

    async def scenario():
        server, _ = await serve(tmp_path, devices=provider)
        try:
            result = await RuntimeClient(tmp_path).devices()
            assert result["devices"] == snapshot["devices"]
        finally:
            await server.close()

    run(scenario())
    if registry:
        registry.sessions["remote"].closed = True
        assert len(provider()["devices"]) == 1


def test_devices_no_provider_invalid_request_and_response_boundary():
    async def scenario():
        server = ControlServer(lambda: {"instance_id": "local"}, lambda: None)
        port = await server.start()
        try:
            assert (await request(port, "devices"))["error"] == "devices_unavailable"
            assert (
                await exchange(port, b'{"id":"1","method":"devices","extra":true}\n')
            )["error"] == "invalid_request"
            assert (await request(port, "ping"))["ok"]
        finally:
            await server.close()
        server = ControlServer(
            lambda: {"instance_id": "local"},
            lambda: None,
            lambda: {"devices": [{"value": "x" * MAX_RESPONSE_BYTES}]},
        )
        port = await server.start()
        try:
            assert (await request(port, "devices"))["error"] == "response_too_large"
        finally:
            await server.close()

    run(scenario())


def test_real_hub_and_agent_devices(tmp_path, monkeypatch):
    import asyncio
    import socket

    from chat2local.runtime import config
    from chat2local.runtime.config import save_persisted_config

    workspace = tmp_path / "shared workspace"
    workspace.mkdir()
    hub_home = tmp_path / "hub home"
    agent_home = tmp_path / "agent home"
    hub_dir = hub_home / ".chat2local"
    agent_dir = agent_home / ".chat2local"
    for directory in (hub_dir, agent_dir):
        directory.mkdir(parents=True)
    token_file = tmp_path / "test-token"
    token_file.write_text("isolated-test-token")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    save_persisted_config(
        {
            "hub": {
                "host": "127.0.0.1",
                "port": port,
                "device_id": "test-hub",
                "token_file": str(token_file),
            }
        },
        hub_dir / "config.yaml",
    )
    save_persisted_config(
        {
            "agent": {
                "hub_url": f"ws://127.0.0.1:{port}/device/ws",
                "device_id": "test-agent",
                "token_file": str(token_file),
                "proxy": "direct",
            }
        },
        agent_dir / "config.yaml",
    )
    hub = RuntimeClient(hub_dir)
    agent = RuntimeClient(agent_dir)

    async def start_isolated(client, home, mode):
        with monkeypatch.context() as isolated:
            isolated.setenv("USERPROFILE", str(home))
            isolated.setenv("HOME", str(home))
            isolated.delenv("CHAT2LOCAL_HUB_TOKEN", raising=False)
            isolated.setattr(
                config, "DEFAULT_CONFIG_PATH", home / ".chat2local/config.yaml"
            )
            await client.action("start", mode, str(workspace))

    async def scenario():
        try:
            await start_isolated(hub, hub_home, "hub")
            assert len((await hub.devices())["devices"]) == 1
            await start_isolated(agent, agent_home, "agent")
            for _ in range(50):
                devices = (await hub.devices())["devices"]
                if len(devices) == 2:
                    break
                await asyncio.sleep(0.1)
            assert [device["device_id"] for device in devices] == [
                "test-hub",
                "test-agent",
            ]
            assert [
                device["device_id"] for device in (await agent.devices())["devices"]
            ] == ["test-agent"]
            assert (await agent.discover())["core"]["state"] == "connected"
            await agent.action("stop")
            for _ in range(20):
                if len((await hub.devices())["devices"]) == 1:
                    break
                await asyncio.sleep(0.1)
            assert len((await hub.devices())["devices"]) == 1
        finally:
            for client in (agent, hub):
                if (await client.discover())["lifecycle"] == "Running":
                    await client.action("stop")

    run(scenario())
