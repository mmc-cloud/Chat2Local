"""Authenticate hello before registering a remote device."""

import asyncio
import secrets

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from chat2local.device.registry import DeviceError, DeviceRegistry, DeviceSession
from chat2local.protocol.messages import HELLO_TIMEOUT, PROTOCOL_VERSION, Hello, HelloAck, Response


async def serve_device(websocket: WebSocket, registry: DeviceRegistry, token: str) -> None:
    await websocket.accept()
    session = None
    try:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), HELLO_TIMEOUT)
            hello = Hello.model_validate_json(raw)
        except (ValidationError, TimeoutError, KeyError):
            raise DeviceError("Invalid hello or unsupported protocol version") from None
        if not secrets.compare_digest(hello.token.encode(), token.encode()):
            raise DeviceError("Invalid Hub token")
        candidate = DeviceSession(hello.device_id, websocket, hello.capabilities)
        registry.register(candidate)
        session = candidate
        await websocket.send_text(HelloAck(protocol_version=PROTOCOL_VERSION, ok=True).model_dump_json())
        while True:
            try:
                raw = await websocket.receive_text()
                response = Response.model_validate_json(raw)
            except (ValidationError, KeyError):
                raise DeviceError("Invalid response or unsupported protocol version") from None
            session.receive(response)
    except DeviceError as error:
        if session is None:
            await websocket.send_text(HelloAck(protocol_version=PROTOCOL_VERSION, ok=False, error=str(error)).model_dump_json())
        await websocket.close(code=1008, reason=str(error)[:120])
    except (WebSocketDisconnect, OSError, RuntimeError):
        pass
    finally:
        if session is not None:
            registry.unregister(session)
