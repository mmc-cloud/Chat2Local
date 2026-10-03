"""In-memory online registry; connections own their pending requests."""

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from chat2local.protocol.messages import PROTOCOL_VERSION, Request, Response

REMOTE_REQUEST_TIMEOUT = 30.0


class DeviceError(RuntimeError):
    pass


class DeviceInfo(BaseModel):
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    kind: Literal["local", "remote"]
    online: bool
    tools: list[str]


class DeviceSession:
    def __init__(self, device_id: str, websocket: WebSocket, capabilities: list[str]) -> None:
        self.device_id = device_id
        self.websocket = websocket
        self.capabilities = tuple(dict.fromkeys(capabilities))
        self.connected_at = datetime.now(timezone.utc)
        self.pending: dict[UUID, asyncio.Future[Response]] = {}
        self._send_lock = asyncio.Lock()
        self.closed = False

    async def execute(self, tool: str, arguments: dict[str, Any], timeout: float) -> dict[str, Any]:
        if self.closed:
            raise DeviceError(f"Device offline: {self.device_id}")
        if tool not in self.capabilities:
            raise DeviceError(f"Device {self.device_id} does not support tool: {tool}")
        request = Request(
            protocol_version=PROTOCOL_VERSION, request_id=uuid4(), tool=tool, arguments=arguments,
        )
        future = asyncio.get_running_loop().create_future()
        self.pending[request.request_id] = future
        try:
            async with asyncio.timeout(timeout):
                async with self._send_lock:
                    if self.closed:
                        raise DeviceError(f"Device offline: {self.device_id}")
                    await self.websocket.send_text(request.model_dump_json())
                response = await future
            if not response.ok:
                raise DeviceError(response.error)
            return response.result
        except TimeoutError as error:
            raise DeviceError(f"Remote request timed out: {self.device_id} ({tool})") from error
        except (OSError, RuntimeError, WebSocketDisconnect) as error:
            if isinstance(error, DeviceError):
                raise
            self.disconnect()
            raise DeviceError(f"Device offline: {self.device_id}") from error
        finally:
            self.pending.pop(request.request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # consume failures even if sending failed first

    def receive(self, response: Response) -> None:
        future = self.pending.get(response.request_id)
        if future is not None and not future.done():
            future.set_result(response)

    def disconnect(self) -> None:
        self.closed = True
        for future in self.pending.values():
            if not future.done():
                future.set_exception(DeviceError(f"Device offline: {self.device_id}"))
        self.pending.clear()


class DeviceRegistry:
    def __init__(self, local_device_id: str) -> None:
        self.local_device_id = local_device_id
        self.sessions: dict[str, DeviceSession] = {}

    def register(self, session: DeviceSession) -> None:
        if session.device_id == self.local_device_id:
            raise DeviceError(f"Device ID conflicts with Hub local device: {session.device_id}")
        if session.device_id in self.sessions:
            raise DeviceError(f"Device already connected: {session.device_id}")
        self.sessions[session.device_id] = session

    def unregister(self, session: DeviceSession) -> None:
        if self.sessions.get(session.device_id) is session:
            del self.sessions[session.device_id]
        session.disconnect()

    def get(self, device_id: str) -> DeviceSession:
        session = self.sessions.get(device_id)
        if session is None or session.closed:
            raise DeviceError(f"Device offline or unavailable: {device_id}")
        return session

    async def close(self) -> None:
        sessions = list(self.sessions.values())
        for session in sessions:
            self.unregister(session)
        await asyncio.gather(
            *(session.websocket.close(code=1001) for session in sessions), return_exceptions=True,
        )
