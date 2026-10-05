"""Connection configuration, retry semantics and safe exception boundaries."""

import asyncio
from contextlib import asynccontextmanager
import logging

import pytest
from websockets.exceptions import ConnectionClosedError, InvalidHandshake, InvalidProxy

from chat2local.agent import client as agent_module
from chat2local.agent.client import AgentClient, RegistrationError
from chat2local.dispatch.local import ToolExecutionError
from chat2local.runtime.config import ConfigError
from conftest import run
from test_devices import (TOKEN, eventually, hub_runtime, running_agent, running_server)
from test_dispatch import make_dispatcher
from test_process_integration import mcp_client
from chat2local.app import create_app


@pytest.mark.parametrize("setting,expected", [("system", True), ("direct", None),
    ("http://user:PRIVATE-PASSWORD@localhost:7897", "http://user:PRIVATE-PASSWORD@localhost:7897")])
def test_proxy_is_explicitly_passed_to_connect(tmp_path, monkeypatch, caplog, setting, expected):
    calls = []
    def connect(url, **kwargs):
        calls.append((url, kwargs))
        raise asyncio.CancelledError
    monkeypatch.setattr(agent_module, "connect", connect)
    caplog.set_level(logging.DEBUG)
    client = AgentClient("ws://localhost/device/ws", "desktop", TOKEN,
                         make_dispatcher(tmp_path), proxy=setting)
    with pytest.raises(asyncio.CancelledError):
        run(client.run())
    assert calls[0][1]["proxy"] is expected or calls[0][1]["proxy"] == expected
    assert calls[0][1]["logger"].getEffectiveLevel() == logging.WARNING
    assert "PRIVATE-PASSWORD" not in caplog.text and TOKEN not in caplog.text
    assert client.state == "offline"


@pytest.mark.parametrize("proxy", ["system", "socks5://user:PRIVATE-PASSWORD@localhost:1080"])
@pytest.mark.parametrize("error,message", [
    (InvalidProxy("ftp://user:PRIVATE-PASSWORD@host", "bad scheme"), "Agent proxy configuration is invalid"),
    (ImportError("PRIVATE-PASSWORD python-socks unavailable"), "Agent SOCKS proxy requires python-socks[asyncio]"),
])
def test_proxy_errors_during_connect_are_fatal_and_safe(tmp_path, monkeypatch, caplog, proxy, error, message):
    calls = []
    @asynccontextmanager
    async def failed_connect(url, **kwargs):
        calls.append(kwargs)
        raise error
        yield
    async def no_retry(delay):
        pytest.fail("Proxy configuration errors must not retry")
    monkeypatch.setattr(agent_module, "connect", failed_connect)
    monkeypatch.setattr(agent_module.asyncio, "sleep", no_retry)
    client = AgentClient("ws://localhost/device/ws", "desktop", TOKEN, make_dispatcher(tmp_path), proxy=proxy)
    with pytest.raises(ConfigError) as failure:
        run(client.run())
    assert str(failure.value) == message and len(calls) == 1
    assert calls[0]["proxy"] == (True if proxy == "system" else proxy)
    assert "PRIVATE-PASSWORD" not in str(failure.value)
    assert "PRIVATE-PASSWORD" not in caplog.text and "retrying" not in caplog.text


def test_unknown_connection_bug_is_not_retried(tmp_path, monkeypatch, caplog):
    attempts = 0
    def connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise TypeError("internal connection bug")
    monkeypatch.setattr(agent_module, "connect", connect)
    with pytest.raises(TypeError):
        run(AgentClient("ws://localhost/device/ws", "desktop", TOKEN, make_dispatcher(tmp_path)).run())
    assert attempts == 1
    records = [record for record in caplog.records if "Unexpected Agent runtime failure" == record.getMessage()]
    assert len(records) == 1 and records[0].exc_info[0] is TypeError


@pytest.mark.parametrize("error", [OSError(5, "PRIVATE-TOKEN"), TimeoutError("PRIVATE-TOKEN"),
                                   InvalidHandshake("PRIVATE-TOKEN"), ConnectionClosedError(None, None)])
def test_connection_errors_retry_with_safe_category_and_no_trace(tmp_path, monkeypatch, caplog, error):
    attempts = 0
    delays = []
    def connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise error
    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(agent_module, "connect", connect)
    monkeypatch.setattr(agent_module.asyncio, "sleep", sleep)
    caplog.set_level(logging.DEBUG, logger="chat2local.agent.client")
    client = AgentClient("ws://localhost/device/ws", "desktop", TOKEN, make_dispatcher(tmp_path))
    with pytest.raises(asyncio.CancelledError):
        run(client.run())
    assert attempts == 2 and delays == [1, 2]
    assert f"Agent connection failed ({type(error).__name__}); retrying in 2s" in caplog.text
    assert "PRIVATE-TOKEN" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_retry_backoff_resets_after_registration_and_logs_recovery(tmp_path, monkeypatch, caplog):
    attempts = 0
    delays = []
    states = []
    @asynccontextmanager
    async def connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("PRIVATE-TOKEN")
        yield object()
    async def hello(ws):
        pass
    async def serve(ws):
        states.append(client.state)
        if attempts == 4:
            raise asyncio.CancelledError
    async def sleep(delay):
        states.append(client.state)
        delays.append(delay)
    monkeypatch.setattr(agent_module, "connect", connect)
    monkeypatch.setattr(agent_module.asyncio, "sleep", sleep)
    caplog.set_level(logging.INFO, logger="chat2local.agent.client")
    client = AgentClient("ws://localhost/device/ws", "desktop", TOKEN, make_dispatcher(tmp_path))
    monkeypatch.setattr(client, "_hello", hello)
    monkeypatch.setattr(client, "_serve", serve)
    with pytest.raises(asyncio.CancelledError):
        run(client.run())
    assert delays == [1, 2, 1]
    assert states == ["reconnecting", "reconnecting", "connected", "reconnecting", "connected"]
    assert caplog.text.count("Agent connected as desktop") == 1
    assert caplog.text.count("Agent reconnected as desktop") == 1
    assert "Agent connection closed; retrying in 1s" in caplog.text
    assert "ConnectionClosed" not in caplog.text


def test_real_connection_closed_exception_after_registration_logs_lost_category(tmp_path, monkeypatch, caplog):
    from websockets.frames import Close
    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield object()
    async def hello(ws):
        pass
    async def serve(ws):
        raise ConnectionClosedError(Close(1011, TOKEN), None)
    async def sleep(delay):
        assert delay == 1
        raise asyncio.CancelledError
    monkeypatch.setattr(agent_module, "connect", connect)
    monkeypatch.setattr(agent_module.asyncio, "sleep", sleep)
    client = AgentClient("ws://localhost/device/ws", "desktop", TOKEN, make_dispatcher(tmp_path))
    monkeypatch.setattr(client, "_hello", hello)
    monkeypatch.setattr(client, "_serve", serve)
    with pytest.raises(asyncio.CancelledError):
        run(client.run())
    assert "Agent connection lost (ConnectionClosedError); retrying in 1s" in caplog.text
    assert "Agent connection closed;" not in caplog.text and TOKEN not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("raw", ["not-json PRIVATE-TOKEN", '{"protocol_version":99,"token":"PRIVATE-TOKEN"}'])
def test_invalid_serving_protocol_is_fatal_without_retry(tmp_path, monkeypatch, caplog, raw):
    attempts = []
    class Socket:
        async def send(self, text):
            pass
        async def recv(self):
            return '{"type":"hello_ack","protocol_version":1,"ok":true}'
        async def __aiter__(self):
            yield raw
    @asynccontextmanager
    async def connect(*args, **kwargs):
        attempts.append(1)
        yield Socket()
    async def no_retry(delay):
        pytest.fail("Protocol errors must not retry")
    monkeypatch.setattr(agent_module, "connect", connect)
    monkeypatch.setattr(agent_module.asyncio, "sleep", no_retry)
    client = AgentClient("ws://localhost/device/ws", "desktop", TOKEN, make_dispatcher(tmp_path))
    with pytest.raises(RegistrationError) as failure:
        run(client.run())
    assert failure.value.reason_code == "protocol_incompatible" and len(attempts) == 1
    assert "Agent protocol incompatible with Hub" in caplog.text
    assert "PRIVATE-TOKEN" not in caplog.text and "retrying" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("raw", ["PRIVATE-TOKEN", '{"type":"hello_ack","protocol_version":99,"ok":true}'])
def test_invalid_hello_ack_is_classified_safely(tmp_path, raw):
    class Socket:
        async def send(self, text):
            pass
        async def recv(self):
            return raw
    client = AgentClient("ws://localhost/device/ws", "desktop", TOKEN, make_dispatcher(tmp_path))
    with pytest.raises(RegistrationError) as failure:
        run(client._hello(Socket()))
    assert failure.value.reason_code == "protocol_incompatible"
    assert str(failure.value) == "Agent protocol incompatible with Hub"


@pytest.mark.parametrize("main_error", [None, RuntimeError("main failed"),
                                       RegistrationError("authentication_failed"), asyncio.CancelledError()])
def test_shutdown_failure_preserves_main_failure(tmp_path, monkeypatch, caplog, main_error):
    client = AgentClient("ws://localhost/device/ws", "desktop", TOKEN, make_dispatcher(tmp_path))
    shutdown_error = RuntimeError("shutdown failed")
    async def main():
        if main_error is not None:
            raise main_error
    async def shutdown():
        raise shutdown_error
    monkeypatch.setattr(client, "_run", main)
    monkeypatch.setattr(client.local.process_manager, "shutdown", shutdown)
    expected = main_error if main_error is not None else shutdown_error
    with pytest.raises(type(expected)) as failure:
        run(client.run())
    assert failure.value is expected
    assert client.state == "offline"
    errors = [record for record in caplog.records if record.getMessage() == "Unexpected Agent shutdown failure"]
    assert len(errors) == 1 and errors[0].exc_info[1] is shutdown_error
    assert errors[0].exc_info[2] is not None


@pytest.mark.parametrize("exception", [RuntimeError, ValueError, TypeError, AttributeError])
@pytest.mark.parametrize("remote", [False, True])
def test_unknown_tool_failure_safe_over_real_mcp_with_local_traceback(tmp_path, caplog, exception, remote):
    async def bug(arguments):
        raise exception("PRIVATE-INTERNAL-DETAIL")
    async def scenario():
        router = hub_runtime(tmp_path)
        local = make_dispatcher(tmp_path) if remote else router.local
        local._handlers["read"] = bug
        async with running_server(create_app(router, hub_token=TOKEN)) as (url, ws_url):
            async with running_agent(AgentClient(ws_url, "desktop", TOKEN, local)) if remote else noop():
                if remote:
                    await eventually(lambda: "desktop" in router.registry.sessions)
                async with mcp_client(url) as session:
                    result = await session.call_tool("read", {"path": ".", "device": "desktop" if remote else None})
                    assert result.is_error
                    text = result.content[0].text
                    assert "Internal local tool error" in text
                    assert "PRIVATE-INTERNAL-DETAIL" not in text and "Traceback" not in text
    run(scenario())
    errors = [record for record in caplog.records if record.name == "chat2local.dispatch.local"
              and record.getMessage().startswith(f"Tool read failed error_type={exception.__name__} duration_ms=")]
    assert len(errors) == 1 and errors[0].exc_info[0] is exception
    assert errors[0].exc_info[2] is not None
    assert "PRIVATE-INTERNAL-DETAIL" in caplog.text


@asynccontextmanager
async def noop():
    yield


def test_business_failure_has_no_traceback(tmp_path, caplog):
    with pytest.raises(ToolExecutionError, match="Path does not exist"):
        run(make_dispatcher(tmp_path).execute("read", {"path": "missing.txt"}))
    assert not any(record.exc_info for record in caplog.records)


def test_validation_error_does_not_echo_request_body(tmp_path):
    with pytest.raises(ToolExecutionError) as failure:
        run(make_dispatcher(tmp_path).execute("read", {"path": {"token": "PRIVATE-TOKEN"}}))
    assert "Invalid arguments" in str(failure.value)
    assert "PRIVATE-TOKEN" not in str(failure.value)


@pytest.mark.parametrize("unexpected", [False, True])
def test_spawn_callback_logs_unknown_traceback_and_safe_expected_error(tmp_path, caplog, unexpected):
    from chat2local.runtime.process_manager import ProcessError
    kind = RuntimeError if unexpected else ProcessError
    async def scenario():
        manager = make_dispatcher(tmp_path).process_manager
        async def fail():
            raise kind("spawn detail")
        task = asyncio.create_task(fail())
        with pytest.raises(kind):
            await task
        manager._spawn_done(task)
    run(scenario())
    records = [record for record in caplog.records if "spawn fail" in record.getMessage()]
    assert len(records) == 1
    if unexpected:
        assert records[0].exc_info[0] is RuntimeError and records[0].exc_info[2] is not None
    else:
        assert records[0].exc_info is None and "spawn detail" not in caplog.text


def test_agent_tool_exception_fallback_has_traceback_and_safe_wire_response(tmp_path, monkeypatch, caplog):
    async def fail(*args):
        raise TypeError("PRIVATE-INTERNAL-DETAIL")
    async def scenario():
        router = hub_runtime(tmp_path)
        local = make_dispatcher(tmp_path)
        monkeypatch.setattr(local, "execute", fail)
        async with running_server(create_app(router, hub_token=TOKEN)) as (url, ws_url):
            async with running_agent(AgentClient(ws_url, "desktop", TOKEN, local)):
                await eventually(lambda: "desktop" in router.registry.sessions)
                async with mcp_client(url) as session:
                    result = await session.call_tool("read", {"path": ".", "device": "desktop"})
                    assert result.is_error and "Internal local tool error" in result.content[0].text
                    assert "PRIVATE-INTERNAL-DETAIL" not in result.content[0].text
    run(scenario())
    records = [record for record in caplog.records if record.name == "chat2local.agent.client"
               and record.getMessage() == "Unexpected local tool failure"]
    assert len(records) == 1 and records[0].exc_info[0] is TypeError


def test_unknown_remote_send_bug_is_not_disguised_as_offline(tmp_path, caplog):
    from chat2local.device.registry import DeviceError, DeviceSession
    from starlette.websockets import WebSocketState
    class BrokenWebSocket:
        application_state = WebSocketState.CONNECTED
        async def send_text(self, data):
            raise RuntimeError("PRIVATE-INTERNAL-DETAIL")
    async def scenario():
        router = hub_runtime(tmp_path)
        session = DeviceSession("desktop", BrokenWebSocket(), ["read"])
        router.registry.register(session)
        with pytest.raises(DeviceError, match="Internal device error") as failure:
            await router.execute("read", {"path": "."}, "desktop")
        assert "PRIVATE-INTERNAL-DETAIL" not in str(failure.value)
        assert session.pending == {} and not session.closed
    run(scenario())
    records = [record for record in caplog.records if record.getMessage() == "Unexpected device routing failure"]
    assert len(records) == 1 and records[0].exc_info[0] is RuntimeError


def test_unknown_hub_registration_bug_closes_with_safe_reason(tmp_path, monkeypatch, caplog):
    from test_devices import hello
    from websockets.asyncio.client import connect
    async def scenario():
        router = hub_runtime(tmp_path)
        def bug(session):
            raise TypeError("PRIVATE-INTERNAL-DETAIL")
        monkeypatch.setattr(router.registry, "register", bug)
        async with running_server(create_app(router, hub_token=TOKEN)) as (_, ws_url):
            async with connect(ws_url, proxy=None) as ws:
                with pytest.raises(ConnectionClosedError) as failure:
                    await hello(ws)
                assert failure.value.rcvd.code == 1011
                assert failure.value.rcvd.reason == "Internal Hub error"
    run(scenario())
    records = [record for record in caplog.records if record.getMessage() == "Unexpected Hub device connection failure"]
    assert len(records) == 1 and records[0].exc_info[0] is TypeError
