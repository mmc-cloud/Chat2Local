"""Connect, register and dispatch generic tool requests; reconnect after disconnect."""

import asyncio
import logging
from urllib.parse import urlsplit

from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from chat2local.device.registry import DeviceError, DeviceInfo
from chat2local.dispatch.local import LocalToolDispatcher, ToolExecutionError
from chat2local.protocol.messages import HELLO_TIMEOUT, PROTOCOL_VERSION, Hello, HelloAck, Request, Response

logger = logging.getLogger(__name__)
MAX_IN_FLIGHT = 16


class RegistrationError(DeviceError):
    """The Hub rejected registration or sent an incompatible acknowledgement."""


class AgentClient:
    def __init__(self, hub_url: str, device_id: str, token: str, local: LocalToolDispatcher) -> None:
        parsed = urlsplit(hub_url)
        if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
            raise ValueError("Hub URL must be ws:// or wss:// with a hostname")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError("Hub URL must not contain credentials or a fragment")
        if not token:
            raise ValueError("Hub token is required")
        DeviceInfo(device_id=device_id, kind="local", online=True, tools=list(local.tools))
        self.hub_url = hub_url
        self.device_id = device_id
        self.token = token
        self.local = local

    async def run(self) -> None:
        # Reconnect retains this device runtime. Only exiting run() owns shutdown.
        try:
            await self._run()
        finally:
            await self.local.process_manager.shutdown()

    async def _run(self) -> None:
        delay = 1.0
        while True:
            try:
                async with connect(self.hub_url, open_timeout=HELLO_TIMEOUT, close_timeout=3) as ws:
                    await self._hello(ws)
                    delay = 1.0
                    logger.info("Agent registered as %s", self.device_id)
                    await self._serve(ws)
            except RegistrationError as error:
                reason = str(error).replace(self.token, "[redacted]")
                reason = " ".join(
                    "".join(char if char.isprintable() else " " for char in reason).split()
                )[:300]
                logger.warning("Registration rejected: %s; reconnecting in %.0fs", reason, delay)
            except (OSError, TimeoutError, ConnectionClosed, InvalidHandshake, DeviceError):
                # Do not log URLs, hello data or exception bodies containing credentials.
                logger.warning("Agent disconnected; reconnecting in %.0fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _hello(self, ws: ClientConnection) -> None:
        await ws.send(Hello(protocol_version=PROTOCOL_VERSION, device_id=self.device_id, token=self.token,
                            capabilities=list(self.local.tools)).model_dump_json())
        try:
            ack = HelloAck.model_validate_json(await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT))
        except ValidationError:
            raise RegistrationError(
                "Invalid Hub hello acknowledgement or unsupported protocol version"
            ) from None
        if not ack.ok:
            raise RegistrationError(ack.error or "Agent registration rejected")

    async def _serve(self, ws: ClientConnection) -> None:
        tasks: set[asyncio.Task] = set()
        send_lock = asyncio.Lock()

        async def respond(request: Request) -> None:
            try:
                result = await self.local.execute(request.tool, request.arguments)
                response = Response(protocol_version=PROTOCOL_VERSION, request_id=request.request_id, ok=True, result=result)
            except ToolExecutionError as error:
                response = Response(protocol_version=PROTOCOL_VERSION, request_id=request.request_id, ok=False, error=str(error))
            except Exception:
                logger.error("Unexpected local tool failure")
                response = Response(protocol_version=PROTOCOL_VERSION, request_id=request.request_id, ok=False, error="Internal local tool error")
            try:
                async with send_lock:
                    await ws.send(response.model_dump_json())
            except ConnectionClosed:
                pass

        try:
            async for raw in ws:
                try:
                    request = Request.model_validate_json(raw)
                except ValidationError:
                    raise DeviceError("Invalid Hub request or unsupported protocol version") from None
                if len(tasks) >= MAX_IN_FLIGHT:
                    async with send_lock:
                        await ws.send(Response(protocol_version=PROTOCOL_VERSION, request_id=request.request_id, ok=False,
                                               error="Agent is busy").model_dump_json())
                    continue
                task = asyncio.create_task(respond(request))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
