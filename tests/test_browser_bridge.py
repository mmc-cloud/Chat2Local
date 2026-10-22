"""Real loopback sockets with a simulated authenticated Chrome extension."""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed, InvalidStatus

from chat2local.browser.bridge import BrowserBridge, CAPABILITY, MAX_IN_FLIGHT
from chat2local.browser.capabilities import CapabilityRegistry
from chat2local.runtime.config import BrowserConfig
from conftest import run
from test_devices import eventually

BROWSER_TOKEN = "test-browser-credential-" + "x" * 32
ORIGIN = "chrome-extension://" + "a" * 32


def browser_config(tmp_path, *, name="browser", token=BROWSER_TOKEN):
    path = tmp_path / f"{name}.token"
    path.write_text(token, encoding="utf-8")
    # Only tests use ephemeral port=0; user config enforces 1..65535.
    return BrowserConfig(enabled=True, extension_id="a" * 32, token_file=str(path)).model_copy(update={"port": 0})


def endpoint(bridge):
    port = bridge.server.sockets[0].getsockname()[1]
    return f"ws://127.0.0.1:{port}/browser/ws"


@asynccontextmanager
async def extension(bridge, *, capabilities=None):
    async with connect(endpoint(bridge), origin=ORIGIN, proxy=None) as ws:
        await ws.send(json.dumps({"type": "hello", "protocol_version": 1, "token": BROWSER_TOKEN,
                                 "capabilities": [CAPABILITY] if capabilities is None else capabilities}))
        assert json.loads(await ws.recv()) == {"type": "hello_ack", "protocol_version": 1, "ok": True}
        yield ws
    await eventually(lambda: bridge._connection is None)


@asynccontextmanager
async def running_bridge(config):
    bridge = BrowserBridge(config)
    await bridge.start()
    try:
        assert bridge.server is not None
        assert all(sock.getsockname()[0] == "127.0.0.1" for sock in bridge.server.sockets)
        yield bridge
    finally:
        await bridge.stop()
        assert not bridge.available and bridge.server is None and not bridge._pending and not bridge._token


def response(request, *, result=None, error=None):
    base = {"type": "response", "protocol_version": 1, "request_id": request["request_id"], "ok": error is None}
    return json.dumps({**base, "result": result or {"tab_id": 42}} if error is None else {**base, "error": error})


@pytest.mark.parametrize("disconnect", [False, True])
def test_capability_unavailable_until_ack_sent(tmp_path, monkeypatch, disconnect):
    async def scenario():
        ack_started, release_ack = asyncio.Event(), asyncio.Event()
        original_send = ServerConnection.send

        async def delayed_ack(ws, message, *args, **kwargs):
            if json.loads(message).get("type") == "hello_ack":
                ack_started.set()
                await release_ack.wait()
            await original_send(ws, message, *args, **kwargs)

        monkeypatch.setattr(ServerConnection, "send", delayed_ack)
        async with running_bridge(browser_config(tmp_path)) as bridge:
            async with connect(endpoint(bridge), origin=ORIGIN, proxy=None) as ws:
                hello = json.dumps({"type": "hello", "protocol_version": 1, "token": BROWSER_TOKEN,
                                    "capabilities": [CAPABILITY]})
                await ws.send(hello)
                try:
                    await asyncio.wait_for(ack_started.wait(), 3)
                    assert bridge._connection is not None  # Identity is reserved during acknowledgement.
                    registry = CapabilityRegistry(bridge)
                    assert registry.discover()["result"]["capabilities"][0]["available"] is False
                    refused = await registry.execute(CAPABILITY, {"url": "https://example.com"})
                    assert refused["error"]["code"] == "capability_unavailable" and not bridge._pending
                    async with connect(endpoint(bridge), origin=ORIGIN, proxy=None) as duplicate:
                        await duplicate.send(hello)
                        with pytest.raises(ConnectionClosed) as failure:
                            await duplicate.recv()
                        assert failure.value.rcvd.code == 1008
                    if disconnect:
                        await ws.close()
                finally:
                    release_ack.set()  # Assertions must not strand the server handler during cleanup.
                if disconnect:
                    await eventually(lambda: bridge._connection is None)
                    assert not bridge.available and not bridge._supported
                else:
                    assert json.loads(await asyncio.wait_for(ws.recv(), 3))["type"] == "hello_ack"
                    await eventually(lambda: bridge.available)
                    pending = asyncio.create_task(registry.execute(CAPABILITY, {"url": "https://example.com"}))
                    request = json.loads(await asyncio.wait_for(ws.recv(), 3))
                    assert request["type"] == "request"
                    await ws.send(response(request))
                    assert (await pending)["ok"] is True
    run(scenario())


@pytest.mark.parametrize("code,expected", [
    ("unknown_capability", "unknown_capability"),
    ("invalid_arguments", "invalid_arguments"),
    ("request_expired", "request_expired"),
    ("browser_operation_failed", "browser_operation_failed"),
    ("not-whitelisted-" + BROWSER_TOKEN, "browser_operation_failed"),
    ("", "browser_operation_failed"),
    (None, "browser_operation_failed"),
])
def test_extension_error_codes_are_allowlisted_and_messages_sanitized(tmp_path, caplog, code, expected):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            async with extension(bridge) as ws:
                pending = asyncio.create_task(bridge.execute(CAPABILITY, {"url": "https://example.com"}))
                request = json.loads(await asyncio.wait_for(ws.recv(), 3))
                error = {"message": BROWSER_TOKEN, "extra": BROWSER_TOKEN}
                if code is not None:
                    error["code"] = code
                await ws.send(response(request, error=error))
                result = await pending
                assert set(result) == {"ok", "error"} and result["ok"] is False
                assert set(result["error"]) == {"code", "message"}
                assert result["error"]["code"] == expected and result["error"]["message"]
                assert BROWSER_TOKEN not in json.dumps(result)
                assert bridge.available and not bridge._pending
    run(scenario())
    assert BROWSER_TOKEN not in caplog.text


def test_request_ids_out_of_order_errors_disconnect_and_reconnect(tmp_path):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            assert not bridge.available
            async with extension(bridge) as ws:
                assert bridge.available
                await ws.send('{"type":"ping"}')
                assert await ws.recv() == '{"type":"pong"}'
                tasks = [asyncio.create_task(bridge.execute(CAPABILITY, {"url": f"https://example.com/{i}"})) for i in range(3)]
                requests = [json.loads(await ws.recv()) for _ in tasks]
                assert len({r["request_id"] for r in requests}) == 3
                assert all(r["capability"] == CAPABILITY and "token" not in r for r in requests)
                await ws.send(response({"request_id": str(uuid4())}))  # Unsolicited ID cannot resolve a caller.
                await ws.send(response(requests[1], error={"code": "failure", "message": BROWSER_TOKEN}))
                await ws.send(response(requests[0], result={"tab_id": 12}))
                assert await tasks[0] == {"ok": True, "result": {"tab_id": 12}}
                assert (await tasks[1])["error"]["code"] == "browser_operation_failed"
                await ws.close()
                assert (await tasks[2])["error"]["code"] == "capability_unavailable"
            assert not bridge.available
            async with extension(bridge, capabilities=[]):
                assert not bridge.available  # Authentication alone is not capability registration.
            async with extension(bridge) as ws:
                pending = asyncio.create_task(bridge.execute(CAPABILITY, {"url": "https://example.com"}))
                request = json.loads(await ws.recv())
                await ws.send(response(request))
                assert (await pending)["result"]["tab_id"] == 42
    run(scenario())


@pytest.mark.parametrize("origin", [None, "null", "https://example.com", "http://127.0.0.1", "chrome-extension://" + "b" * 32])
def test_origin_rejected_before_authentication(tmp_path, origin):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            with pytest.raises(InvalidStatus) as failure:
                async with connect(endpoint(bridge), origin=origin, proxy=None):
                    pytest.fail("Untrusted origin accepted")
            assert failure.value.response.status_code == 403
            assert not bridge.available
    run(scenario())


@pytest.mark.parametrize("hello", [
    {"type": "hello", "protocol_version": 1, "token": "wrong-" + "x" * 40, "capabilities": [CAPABILITY]},
    {"type": "hello", "protocol_version": 1, "token": BROWSER_TOKEN, "capabilities": ["browser.page.click"]},
    {"type": "request", "protocol_version": 1, "capability": CAPABILITY, "arguments": {"url": "https://example.com"}},
    {"type": "hello", "protocol_version": 2, "token": BROWSER_TOKEN, "capabilities": [CAPABILITY]},
    {"type": "hello", "protocol_version": 1, "token": "密" * 40, "capabilities": [CAPABILITY]},
    "not json",
])
def test_invalid_or_unauthenticated_protocol_is_rejected(tmp_path, hello, caplog):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            async with connect(endpoint(bridge), origin=ORIGIN, proxy=None) as ws:
                await ws.send(hello if isinstance(hello, str) else json.dumps(hello))
                with pytest.raises(ConnectionClosed) as failure:
                    await ws.recv()
                assert failure.value.rcvd.code == 1008 and not bridge.available
    with caplog.at_level(logging.DEBUG):
        run(scenario())
    assert BROWSER_TOKEN not in caplog.text and "wrong-" not in caplog.text


def test_duplicate_connection_and_wrong_path_rejected(tmp_path):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            with pytest.raises(InvalidStatus) as failure:
                async with connect(endpoint(bridge) + "?token=not-allowed", origin=ORIGIN, proxy=None):
                    pass
            assert failure.value.response.status_code == 404
            async with extension(bridge):
                async with connect(endpoint(bridge), origin=ORIGIN, proxy=None) as ws:
                    await ws.send(json.dumps({"type": "hello", "protocol_version": 1, "token": BROWSER_TOKEN,
                                              "capabilities": [CAPABILITY]}))
                    with pytest.raises(ConnectionClosed) as failure:
                        await ws.recv()
                    assert failure.value.rcvd.code == 1008
                assert bridge.available
    run(scenario())


def test_timeout_cancel_limit_and_late_response_cleanup(tmp_path):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            async with extension(bridge) as ws:
                bridge.request_timeout = 0.05
                pending = asyncio.create_task(bridge.execute(CAPABILITY, {}))
                request = json.loads(await ws.recv())
                assert (await pending)["error"]["code"] == "capability_timeout"
                assert not bridge._pending
                await ws.send(response(request))  # Late replies do not change any other request.
                bridge.request_timeout = 5
                tasks = [asyncio.create_task(bridge.execute(CAPABILITY, {})) for _ in range(MAX_IN_FLIGHT)]
                for _ in tasks:
                    await ws.recv()
                assert (await bridge.execute(CAPABILITY, {}))["error"]["code"] == "capability_busy"
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                assert not bridge._pending
                pending = asyncio.create_task(bridge.execute(CAPABILITY, {}))
                await ws.recv()
                # Shutdown releases a waiter and the listening socket.
                await bridge.stop()
                assert (await pending)["error"]["code"] == "capability_unavailable"
    run(scenario())


@pytest.mark.parametrize("problem", ["missing", "invalid", "hub-token", "port-in-use", "disabled"])
def test_optional_bridge_start_failure_does_not_stop_core(tmp_path, problem, caplog):
    async def scenario():
        config = browser_config(tmp_path)
        if problem == "missing":
            config = config.model_copy(update={"token_file": str(tmp_path / "missing")})
        if problem == "invalid":
            config = browser_config(tmp_path, token="do-not-log-this-secret")
        if problem == "disabled":
            config = BrowserConfig()
        bridge = BrowserBridge(config)
        other = None
        try:
            if problem == "port-in-use":
                other = BrowserBridge(config)
                await other.start()
                bridge.config = config.model_copy(update={"port": other.server.sockets[0].getsockname()[1]})
            await bridge.start(hub_token=BROWSER_TOKEN if problem == "hub-token" else "different-hub-token")
            assert bridge.server is None and not bridge.available and not bridge._token
        finally:
            await bridge.stop()
            if other:
                await other.stop()
    run(scenario())
    assert BROWSER_TOKEN not in caplog.text and "do-not-log-this-secret" not in caplog.text
