"""Real loopback browser status IPC, using only temporary test runtimes."""
import asyncio
import importlib
import json
from pathlib import Path
import socket

import pytest

from chat2local.browser.bridge import BrowserBridge
from chat2local.management.adapter import CoreAdapter
from chat2local.management.runtime import RuntimeClient, ManagementError
from chat2local.runtime import config
from chat2local.runtime.supervisor import RuntimeSupervisor
from conftest import run
from test_browser_bridge import browser_config, extension
from test_devices import eventually
from test_management_runtime import serve


def test_browser_connection_states_over_real_control_socket(tmp_path, monkeypatch):
    settings = browser_config(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        settings = settings.model_copy(update={"port": listener.getsockname()[1]})
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    config.save_persisted_config({"browser": settings.model_dump()})

    async def scenario():
        bridge = BrowserBridge(settings)
        directory = tmp_path / "runtime"
        supervisor = RuntimeSupervisor("standalone", "test", tmp_path, state=lambda: "running",
                                       directory=directory, browser=bridge.snapshot)
        client = RuntimeClient(directory)
        adapter = CoreAdapter(client)
        async def core():
            try:
                await bridge.start()
                await asyncio.Event().wait()
            finally:
                await bridge.stop()
        task = asyncio.create_task(supervisor.run(core, lambda task: task.cancel()))
        try:
            await eventually(lambda: bridge.server is not None)
            assert (await client.browser())["state"] == "listening"
            assert (await adapter.browser_status())["state"] == "listening"
            async with extension(bridge):
                assert (await adapter.browser_status())["state"] == "connected"
                assert bridge._token not in json.dumps(await client.browser())
                Path(settings.token_file).write_text("r" * 43)
                assert (await adapter.browser_status())["state"] == "restart_required"
            await eventually(lambda: bridge._connection is None)
            assert bridge.snapshot()["state"] == "listening"
            await adapter.save_config({"browser": {"enabled": False}})
            assert (await adapter.browser_status())["state"] == "restart_required"
        finally:
            supervisor.request_stop()
            await task
        assert (await adapter.browser_status())["state"] == "disabled"
    run(scenario())


def test_disabled_failed_stopped_and_legacy_core_are_not_reported_connected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    async def scenario():
        bridge = BrowserBridge(config.BrowserConfig())
        assert bridge.snapshot()["state"] == "disabled"
        broken = BrowserBridge(browser_config(tmp_path).model_copy(update={"token_file": str(tmp_path / "missing")}))
        await broken.start()
        assert broken.snapshot()["state"] == "failed"
        server, _ = await serve(tmp_path / "legacy")
        try:
            adapter = CoreAdapter(RuntimeClient(tmp_path / "legacy"))
            assert (await adapter.browser_status())["state"] == "unavailable"
        finally:
            await server.close()
    run(scenario())
