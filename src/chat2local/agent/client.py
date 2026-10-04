"""Connect, register and dispatch generic tool requests; reconnect after disconnect."""

import asyncio
import logging
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidProxy, InvalidURI
from websockets.uri import parse_uri

from chat2local.device.registry import DeviceError, DeviceInfo
from chat2local.dispatch.local import LocalToolDispatcher, ToolExecutionError
from chat2local.protocol.messages import HELLO_TIMEOUT, PROTOCOL_VERSION, Hello, HelloAck, Request, Response
from chat2local.runtime.config import ConfigError, validate_agent_proxy, validation_message

logger = logging.getLogger(__name__)
MAX_IN_FLIGHT = 16
# Never enable third-party frame/handshake dumps, even with project --debug.
transport_logger = logging.getLogger("chat2local.agent.transport")
transport_logger.setLevel(logging.WARNING)


class RegistrationError(DeviceError):
    """The Hub rejected registration or sent an incompatible acknowledgement."""

    def __init__(self, reason_code: Literal[
        "authentication_failed", "device_already_connected", "device_id_conflict",
        "protocol_incompatible", "registration_rejected",
    ] = "registration_rejected") -> None:
        self.reason_code = reason_code
        messages = {
            "authentication_failed": "Agent authentication failed: invalid Hub token",
            "device_already_connected": "Device already connected",
            "device_id_conflict": "Agent device_id conflict with Hub local device",
            "protocol_incompatible": "Agent protocol incompatible with Hub",
            "registration_rejected": "Registration rejected",
        }
        super().__init__(messages[reason_code])

    @property
    def retryable(self) -> bool:
        return self.reason_code == "device_already_connected"


class AgentClient:
    def __init__(
        self, hub_url: str, device_id: str, token: str, local: LocalToolDispatcher,
        *, proxy: str = "system",
    ) -> None:
        try:
            parsed = parse_uri(hub_url)
            if parsed.user_info is not None or any(char.isspace() or not char.isprintable() for char in hub_url):
                raise ValueError
            if urlsplit(hub_url).port == 0 or not 1 <= parsed.port <= 65535:
                raise ValueError
        except (InvalidURI, ValueError):
            raise ConfigError("Hub URL must be ws:// or wss:// with a valid hostname/port and no credentials or fragment") from None
        if not token:
            raise ValueError("Hub token is required")
        try:
            DeviceInfo(device_id=device_id, kind="local", online=True, tools=list(local.tools))
        except ValidationError as error:
            raise ConfigError(validation_message(error)) from None
        validate_agent_proxy(proxy)
        self.proxy = True if proxy == "system" else None if proxy == "direct" else proxy
        self.hub_url = hub_url
        self.device_id = device_id
        self.token = token
        self.local = local
        self.state = "offline"

    async def run(self) -> None:
        # Reconnect retains this device runtime. Only exiting run() owns shutdown.
        logger.info("Agent starting as %s", self.device_id)
        main_failed = False
        try:
            await self._run()
        except BaseException as error:
            main_failed = True
            if isinstance(error, Exception) and not isinstance(error, (ConfigError, RegistrationError)):
                logger.exception("Unexpected Agent runtime failure")
            raise
        finally:
            try:
                await self.local.process_manager.shutdown()
            except BaseException:
                logger.exception("Unexpected Agent shutdown failure")
                if not main_failed:
                    raise
            finally:
                self.state = "offline"
                logger.info("Agent stopped as %s", self.device_id)

    async def _run(self) -> None:
        delay = 1.0
        ever_connected = False
        while True:
            connected = False
            connection_opened = False
            self.state = "connecting" if not ever_connected else "reconnecting"
            logger.debug("Agent connection phase=%s", self.state)
            try:
                async with connect(self.hub_url, proxy=self.proxy, logger=transport_logger,
                                   open_timeout=HELLO_TIMEOUT, close_timeout=3) as ws:
                    connection_opened = True
                    await self._hello(ws)
                    delay = 1.0
                    connected = True
                    self.state = "connected"
                    logger.info("Agent %s as %s", "reconnected" if ever_connected else "connected", self.device_id)
                    ever_connected = True
                    await self._serve(ws)
                self.state = "reconnecting"
                logger.warning("Agent connection closed; retrying in %.0fs", delay)
            except RegistrationError as error:
                self.state = error.reason_code
                # This error contains only our fixed message, never arbitrary Hub text.
                if not error.retryable:
                    logger.error("%s", error)
                    raise
                logger.warning("%s; retrying in %.0fs", error, delay)
            except (OSError, TimeoutError, ConnectionClosed, InvalidHandshake, DeviceError) as error:
                # Do not log URLs, hello data or exception bodies containing credentials.
                self.state = "reconnecting"
                logger.warning("Agent connection %s (%s); retrying in %.0fs",
                               "lost" if connected else "failed", type(error).__name__, delay)
                close_code = error.rcvd.code if isinstance(error, ConnectionClosed) and error.rcvd else None
                logger.debug("Agent connection phase=%s errno=%s close_code=%s",
                             "serving" if connected else "connecting", getattr(error, "errno", None), close_code)
            except InvalidProxy:
                if connection_opened:
                    raise
                self.state = "configuration_error"
                raise ConfigError("Agent proxy configuration is invalid") from None
            except ImportError:
                if connection_opened:
                    raise
                self.state = "configuration_error"
                raise ConfigError("Agent SOCKS proxy requires python-socks[asyncio]") from None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _hello(self, ws: ClientConnection) -> None:
        await ws.send(Hello(protocol_version=PROTOCOL_VERSION, device_id=self.device_id, token=self.token,
                            capabilities=list(self.local.tools)).model_dump_json())
        try:
            ack = HelloAck.model_validate_json(await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT))
        except ValidationError:
            raise RegistrationError("protocol_incompatible") from None
        if not ack.ok:
            # Match only messages generated by our Hub, including the exact requested ID.
            codes = {
                "Invalid Hub token": "authentication_failed",
                f"Device already connected: {self.device_id}": "device_already_connected",
                f"Device ID conflicts with Hub local device: {self.device_id}": "device_id_conflict",
                "Invalid hello or unsupported protocol version": "protocol_incompatible",
                "Invalid Hub hello acknowledgement or unsupported protocol version": "protocol_incompatible",
            }
            raise RegistrationError(codes.get(ack.error, "registration_rejected"))

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
                logger.exception("Unexpected local tool failure")
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
                    raise RegistrationError("protocol_incompatible") from None
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
