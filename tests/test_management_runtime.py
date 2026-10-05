"""Client boundary and lifecycle behavior using isolated runtimes."""

import asyncio
import json
import subprocess
import sys
from dataclasses import asdict
from unittest.mock import Mock

import pytest
from conftest import run

from chat2local.management.runtime import ManagementError, RuntimeClient
from chat2local.runtime import config
from chat2local.runtime.config import AppConfig
from chat2local.runtime.control import ControlServer
from chat2local.runtime.instance import RuntimeDescriptor


async def serve(
    directory, *, state="connected", instance=None, stop=None, devices=None
):
    directory.mkdir(exist_ok=True)
    descriptor = RuntimeDescriptor.new("agent", "local", directory, 0)
    status = asdict(descriptor)
    status.pop("schema_version")
    status.pop("control")
    status["state"] = state
    if instance:
        status["instance_id"] = instance
    server = ControlServer(lambda: status, stop or (lambda: None), devices)
    port = await server.start()
    descriptor = RuntimeDescriptor(
        **{
            **asdict(descriptor),
            "control": {"transport": "tcp", "host": "127.0.0.1", "port": port},
        }
    )
    descriptor.publish(directory)
    return server, descriptor


def test_no_descriptor_and_invalid_descriptor(tmp_path):
    client = RuntimeClient(tmp_path)
    assert run(client.status())["lifecycle"] == "Stopped"
    (tmp_path / "runtime.json").write_text("not JSON")
    assert run(client.status())["lifecycle"] == "Stale"


@pytest.mark.parametrize(
    "state", ["connected", "connecting", "reconnecting", "running"]
)
def test_verified_discovery_and_core_state(tmp_path, state):
    async def scenario():
        server, descriptor = await serve(tmp_path, state=state)
        try:
            result = await RuntimeClient(tmp_path).status()
            assert result["lifecycle"] == "Running"
            assert result["core"]["instance_id"] == descriptor.instance_id
            assert result["core"]["state"] == state
        finally:
            await server.close()

    run(scenario())


def test_mismatch_and_unavailable_are_stale(tmp_path):
    async def scenario():
        server, _ = await serve(tmp_path, instance="other-instance")
        client = RuntimeClient(tmp_path, control_timeout=0.05)
        try:
            assert (await client.status())["lifecycle"] == "Stale"
        finally:
            await server.close()
        assert (await client.status())["lifecycle"] == "Stale"

    run(scenario())


def test_control_timeout_is_bounded(tmp_path):
    async def scenario():
        async def idle(reader, writer):
            await reader.read()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(idle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        descriptor = RuntimeDescriptor.new("standalone", "local", tmp_path, port)
        descriptor.publish(tmp_path)
        try:
            assert (await RuntimeClient(tmp_path, control_timeout=0.01).status())[
                "lifecycle"
            ] == "Stale"
        finally:
            server.close()
            await server.wait_closed()

    run(scenario())


def test_stop_acknowledgement_and_timeout(tmp_path):
    async def scenario():
        stopped = []

        def stop():
            stopped.append(True)
            (tmp_path / "runtime.json").unlink()

        server, _ = await serve(tmp_path, stop=stop)
        try:
            client = RuntimeClient(tmp_path, timeout=0.03)
            assert (await client.action("stop"))["lifecycle"] == "Stopped"
            assert stopped == [True]
        finally:
            await server.close()
        server, _ = await serve(tmp_path)
        try:
            with pytest.raises(ManagementError, match="stop timed out"):
                await client.action("stop")
        finally:
            await server.close()

    run(scenario())


@pytest.mark.parametrize(
    "mode,subcommand", [("standalone", []), ("hub", ["hub"]), ("agent", ["agent"])]
)
def test_start_args_and_detached_stdio(tmp_path, monkeypatch, mode, subcommand):
    workspace = tmp_path / "a workspace with spaces"
    workspace.mkdir()
    client = RuntimeClient(tmp_path)
    child = Mock(pid=123, poll=Mock(return_value=None))
    spawn = Mock(return_value=child)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(config, "load_config", lambda: AppConfig())
    snapshots = iter(
        [
            {"lifecycle": "Stopped", "core": None},
            {
                "lifecycle": "Running",
                "core": {
                    "mode": mode,
                    "workspace": str(workspace.resolve()),
                    "instance_id": "new",
                    "state": "running",
                },
            },
        ]
    )

    async def discover():
        return next(snapshots)

    monkeypatch.setattr(client, "discover", discover)
    assert run(client.action("start", mode, str(workspace)))["lifecycle"] == "Running"
    args, options = spawn.call_args
    assert args[0] == [
        sys.executable,
        "-m",
        "chat2local.main",
        *subcommand,
        "--workspace",
        str(workspace),
    ]
    assert options["cwd"] == workspace.resolve()
    assert (
        options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
    )
    assert options.get("shell", False) is False
    assert (
        options.get("creationflags") == subprocess.CREATE_NO_WINDOW
        if sys.platform == "win32"
        else options["start_new_session"]
    )
    child.terminate.assert_not_called()
    child.kill.assert_not_called()


def test_start_already_running_does_not_spawn(tmp_path, monkeypatch):
    async def scenario():
        server, _ = await serve(tmp_path)
        try:
            spawn = Mock(side_effect=AssertionError("must not spawn"))
            monkeypatch.setattr(subprocess, "Popen", spawn)
            with pytest.raises(ManagementError, match="already running"):
                await RuntimeClient(tmp_path).action("start", "hub", str(tmp_path))
        finally:
            await server.close()

    run(scenario())


@pytest.mark.parametrize(
    "exit_code,expected", [(2, "failed to start"), (None, "startup timed out")]
)
def test_start_early_exit_and_timeout(tmp_path, monkeypatch, exit_code, expected):
    client = RuntimeClient(tmp_path, timeout=0.01)
    monkeypatch.setattr(config, "load_config", lambda: AppConfig())
    child = Mock(pid=123, poll=Mock(return_value=exit_code))
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=child))
    with pytest.raises(ManagementError, match=expected):
        run(client.action("start", "standalone", str(tmp_path)))
    assert run(client.status())["lifecycle"] == "Error"
    child.kill.assert_not_called()
    child.terminate.assert_not_called()


def test_restart_preserves_current_context_and_serializes(tmp_path, monkeypatch):
    client = RuntimeClient(tmp_path)
    calls = []

    async def discover():
        return {
            "lifecycle": "Running",
            "core": {"mode": "agent", "workspace": "space path"},
        }

    async def stop():
        calls.append("stop")
        return {"lifecycle": "Stopped", "core": None}

    async def start(mode, workspace):
        calls.append((mode, workspace))
        return {"lifecycle": "Running", "core": {}}

    monkeypatch.setattr(client, "discover", discover)
    monkeypatch.setattr(client, "_stop", stop)
    monkeypatch.setattr(client, "_start", start)
    run(client.action("restart"))
    assert calls == ["stop", ("agent", "space path")]
    client._operation.acquire()
    try:
        with pytest.raises(ManagementError, match="in progress"):
            run(client.action("start"))
    finally:
        client._operation.release()


def test_descriptor_never_dials_external_host(tmp_path):
    descriptor = asdict(RuntimeDescriptor.new("standalone", "local", tmp_path, 8765))
    descriptor["control"]["host"] = "example.com"
    (tmp_path / "runtime.json").write_text(json.dumps(descriptor))
    assert run(RuntimeClient(tmp_path).status())["lifecycle"] == "Stale"


def test_core_survives_launcher_exit(tmp_path, monkeypatch):
    import os

    home = tmp_path / "isolated home"
    home.mkdir()
    workspace = home / "workspace"
    workspace.mkdir()
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home), "PYTHONUTF8": "1"}
    bootstrap = """
import asyncio
from chat2local.management.runtime import RuntimeClient
from chat2local.management import runtime
original = runtime.subprocess.Popen
def isolated(args, **kwargs):
    return original([*args, '--port', '0'], **kwargs)
runtime.subprocess.Popen = isolated
asyncio.run(RuntimeClient().action('start', 'standalone', __import__('sys').argv[1]))
"""
    directory = home / ".chat2local"
    client = RuntimeClient(directory)
    try:
        result = subprocess.run(
            [sys.executable, "-c", bootstrap, str(workspace)],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=35,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert run(client.discover())["lifecycle"] == "Running"
    finally:
        if run(client.discover())["lifecycle"] == "Running":
            assert run(client.action("stop"))["lifecycle"] == "Stopped"


def test_timed_out_launch_can_be_discovered_later(tmp_path, monkeypatch):
    client = RuntimeClient(tmp_path)

    async def running():
        return {"lifecycle": "Running", "core": {"instance_id": "new"}}

    monkeypatch.setattr(client, "discover", running)
    client.error = "Core startup timed out"
    client._failed_phase = "Starting"
    assert run(client.status())["lifecycle"] == "Running"
    client.error = "Core stop timed out"
    client._failed_phase = "Stopping"
    assert run(client.status())["lifecycle"] == "Error"
