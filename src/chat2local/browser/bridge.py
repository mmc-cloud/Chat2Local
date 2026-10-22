"""Authenticated loopback WebSocket owned by the existing device runtime."""

import asyncio
from http import HTTPStatus
import hmac
import logging
from pathlib import Path
import re
import time
from typing import Any, Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from chat2local.runtime.config import BrowserConfig
from chat2local.browser.errors import CAPABILITY_NAMES, EXTENSION_ERROR_MESSAGES, extension_failure, failure

logger = logging.getLogger(__name__)
# Never let transport DEBUG dump authentication frames, including with --debug.
transport_logger = logging.getLogger("chat2local.browser.transport")
transport_logger.setLevel(logging.WARNING)
CAPABILITY = "browser.tabs.create"
REQUEST_TIMEOUT = 15.0  # Below the existing 30-second Hub request deadline.
MAX_IN_FLIGHT = 16
class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_version: Literal[1]


class ExtensionHello(_Message):
    type: Literal["hello"]
    token: str = Field(min_length=32, max_length=128, repr=False)
    capabilities: list[str] = Field(max_length=64)

    @model_validator(mode="after")
    def known_capabilities(self) -> Self:
        if len(set(self.capabilities)) != len(self.capabilities) or any(
            name not in CAPABILITY_NAMES for name in self.capabilities
        ):
            raise ValueError("Unsupported capability declaration")
        return self


class ExtensionResponse(_Message):
    type: Literal["response"]
    request_id: UUID
    ok: bool
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if self.ok and (self.result is None or self.error is not None):
            raise ValueError("Successful response requires result only")
        if not self.ok and (self.result is not None or self.error is None):
            raise ValueError("Failed response requires error only")
        return self


class BrowserBridge:
    def __init__(self, config: BrowserConfig) -> None:
        self.config = config
        self.server: Server | None = None
        self._connection: ServerConnection | None = None
        self._supported: set[str] = set()
        self._pending: dict[str, asyncio.Future] = {}
        self._token = ""
        self.request_timeout = REQUEST_TIMEOUT

    @property
    def available(self) -> bool:
        """Compatibility shortcut for the Phase 1 tabs.create provider."""
        return self.is_available(CAPABILITY)

    def is_available(self, name: str) -> bool:
        return (self._connection is not None and self._connection.close_code is None
                and name in self._supported)

    async def start(self, *, hub_token: str | None = None) -> None:
        if not self.config.enabled or self.server is not None:
            return
        try:
            # This is a private configuration credential, not a workspace file Tool.
            with Path(self.config.token_file).expanduser().open(encoding="utf-8") as handle:
                token = handle.read(4096).strip()
            if (re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token) is None
                    or (hub_token is not None and hmac.compare_digest(token.encode(), hub_token.encode()))):
                raise ValueError("Invalid browser credential")
            self._token = token
            self.server = await serve(
                self._handle, "127.0.0.1", self.config.port,
                origins=[f"chrome-extension://{self.config.extension_id}"],
                process_request=self._check_request, logger=transport_logger,
                max_size=64 * 1024, max_queue=16, open_timeout=5,
                ping_interval=20, ping_timeout=20, close_timeout=2,
            )
            logger.info("Browser bridge listening on 127.0.0.1:%d", self.config.port)
        except (OSError, ValueError, RuntimeError):
            self._token = ""
            # Optional listener failures must never stop Core/Hub/Agent.
            logger.warning("Browser bridge unavailable; check browser token file and loopback port")

    def _check_request(self, connection, request):
        if request.path != "/browser/ws":
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        return None  # websockets enforces the exact Origin allowlist.

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        self._token = ""

    async def _handle(self, ws: ServerConnection) -> None:
        try:
            async with asyncio.timeout(5):
                hello = ExtensionHello.model_validate_json(await ws.recv())
            if not hmac.compare_digest(hello.token.encode(), self._token.encode()):
                await ws.close(1008, "Authentication failed")
                return
            if self._connection is not None:
                await ws.close(1008, "Extension already connected")
                return
            self._connection = ws
            # Reserve the connection while acknowledging, but do not publish it as
            # executable yet. Ordered frames and the extension's synchronous ack
            # handling keep the first command behind successful authentication.
            await ws.send('{"type":"hello_ack","protocol_version":1,"ok":true}')
            self._supported = set(hello.capabilities)
            logger.info("Browser extension connected")
            async for raw in ws:
                if raw == '{"type":"ping"}':
                    await ws.send('{"type":"pong"}')
                    continue
                response = ExtensionResponse.model_validate_json(raw)
                future = self._pending.get(str(response.request_id))
                if future is not None and not future.done():
                    if response.ok:
                        result = {"ok": True, "result": response.result}
                    else:
                        result = extension_failure(response.error or {})
                    future.set_result(result)
        except (ValidationError, TimeoutError):
            await ws.close(1008, "Invalid browser protocol")
        except ConnectionClosed:
            pass
        finally:
            if self._connection is ws:
                self._connection = None
                self._supported.clear()
                for future in self._pending.values():
                    if not future.done():
                        future.set_result(failure("capability_unavailable", "Browser extension disconnected; verify tabs before retrying"))
                logger.info("Browser extension disconnected")

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        import json

        ws = self._connection
        if not self.is_available(name) or ws is None:
            return failure("capability_unavailable", "Browser extension is not connected or capability is unavailable")
        if len(self._pending) >= MAX_IN_FLIGHT:
            return failure("capability_busy", "Browser request limit reached")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(self.request_timeout):
                await ws.send(json.dumps({
                    "type": "request", "protocol_version": 1, "request_id": request_id,
                    "capability": name, "arguments": arguments,
                    "expires_at": int((time.time() + self.request_timeout) * 1000),
                }))
                return await future
        except TimeoutError:
            return failure("capability_timeout", "Browser response timed out; verify tabs before retrying")
        except ConnectionClosed:
            return failure("capability_unavailable", "Browser extension disconnected; verify tabs before retrying")
        finally:
            self._pending.pop(request_id, None)
