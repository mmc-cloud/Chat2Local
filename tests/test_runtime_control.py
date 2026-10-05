"""Exercise the actual loopback TCP wire, including bounded invalid clients."""

import asyncio
import json
import socket

import pytest

from chat2local.runtime import control
from chat2local.runtime.control import ControlServer, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES
from conftest import run


async def exchange(port, payload):
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2)
    try:
        writer.write(payload)
        await writer.drain()
        response = await asyncio.wait_for(reader.readline(), 2)
        assert len(response) <= MAX_RESPONSE_BYTES
        assert await asyncio.wait_for(reader.read(), 2) == b""
        return json.loads(response)
    finally:
        writer.close()
        await writer.wait_closed()


async def request(port, method, request_id="1"):
    return await exchange(port, json.dumps({"id": request_id, "method": method}).encode() + b"\n")


def test_ping_status_stop_and_ipv4_loopback_only():
    async def scenario():
        stopped = []
        status = {"instance_id": "instance", "state": "running"}
        server = ControlServer(lambda: status.copy(), lambda: stopped.append(True))
        port = await server.start()
        try:
            assert len(server._server.sockets) == 1
            assert server._server.sockets[0].family == socket.AF_INET
            assert server._server.sockets[0].getsockname() == ("127.0.0.1", port)
            assert await request(port, "ping") == {"id": "1", "ok": True, "result": {"instance_id": "instance"}}
            assert await request(port, "status", "2") == {"id": "2", "ok": True, "result": status}
            assert await request(port, "stop", "3") == {"id": "3", "ok": True, "result": {"accepted": True}}
            assert stopped == [True]
        finally:
            await server.close()
    run(scenario())


@pytest.mark.parametrize("raw", [b"not JSON\n", b"\xff\n", b"[]\n", b"{}\n", b'null\n',
    b'{"id":"1","method":"exec"}\n', b'{"id":1,"method":"ping"}\n',
    b'{"id":"1","method":"ping","token":"SECRET"}\n', b'{"id":"1","method":"read"}\n',
    b'{"id":"","method":"ping"}\n', b'{"id":"1","method":null}\n'])
def test_invalid_json_schema_and_fixed_methods(raw, caplog):
    async def scenario():
        server = ControlServer(lambda: {"instance_id": "instance"}, lambda: pytest.fail("invalid stop"))
        port = await server.start()
        try:
            assert await exchange(port, raw) == {"id": None, "ok": False, "error": "invalid_request"}
            assert (await request(port, "ping"))["ok"]
        finally:
            await server.close()
    run(scenario())
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize("newline", [True, False])
def test_oversized_request(newline):
    async def scenario():
        server = ControlServer(lambda: {}, lambda: None)
        port = await server.start()
        try:
            response = await exchange(port, b"x" * (MAX_REQUEST_BYTES + 1) + (b"\n" if newline else b""))
            assert response == {"id": None, "ok": False, "error": "request_too_large"}
        finally:
            await server.close()
    run(scenario())


def test_timeout_disconnect_and_partial_message(monkeypatch):
    monkeypatch.setattr(control, "CONTROL_TIMEOUT", 0.05)
    async def scenario():
        server = ControlServer(lambda: {"instance_id": "instance"}, lambda: None)
        port = await server.start()
        try:
            assert await exchange(port, b"") == {"id": None, "ok": False, "error": "request_timeout"}
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b'{"id":"1"}')
            await writer.drain()
            writer.write_eof()
            assert json.loads(await reader.readline())["error"] == "invalid_request"
            writer.close()
            await writer.wait_closed()
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            assert (await request(port, "ping"))["ok"]
        finally:
            await server.close()
        assert not server._connections
    run(scenario())


def test_response_limit():
    async def scenario():
        server = ControlServer(lambda: {"instance_id": "id", "workspace": "x" * MAX_RESPONSE_BYTES}, lambda: None)
        port = await server.start()
        try:
            assert (await request(port, "status"))["error"] == "response_too_large"
        finally:
            await server.close()
    run(scenario())


def test_close_cleans_up_idle_connections():
    async def scenario():
        server = ControlServer(lambda: {}, lambda: None)
        port = await server.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await server.close()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await writer.wait_closed()
        assert not server._connections
    run(scenario())
