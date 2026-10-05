"""Bounded one-request-per-connection JSON control, IPv4 loopback only."""

import asyncio
from collections.abc import Callable
import json
import logging
import socket
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 16384
CONTROL_TIMEOUT = 5.0
logger = logging.getLogger(__name__)


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=128)
    method: Literal["ping", "status", "stop"]


class ControlServer:
    def __init__(self, status: Callable[[], dict], stop: Callable[[], None]) -> None:
        self.status = status
        self.stop = stop
        self._server = None
        self._connections: dict[asyncio.Task, asyncio.StreamWriter] = {}
        self._closing = False

    async def start(self) -> int:
        self._server = await asyncio.start_server(
            self._accept, host="127.0.0.1", port=0, family=socket.AF_INET,
            limit=MAX_REQUEST_BYTES,
        )
        return self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        self._closing = True
        if self._server is not None:
            self._server.close()
        connections = tuple(self._connections.items())
        for task, writer in connections:
            writer.close()
            task.cancel()
        await asyncio.gather(*(task for task, _ in connections), return_exceptions=True)
        if self._server is not None:
            # Python 3.12 wait_closed also waits for active transports. Close
            # clients first, otherwise idle readers hold shutdown until timeout.
            await self._server.wait_closed()

    def _accept(self, reader, writer) -> None:
        if self._closing:
            writer.close()
            return
        task = asyncio.create_task(self._handle(reader, writer))
        self._connections[task] = writer
        task.add_done_callback(lambda completed: self._connections.pop(completed, None))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        request_id = None
        stopping = False
        try:
            try:
                raw = await asyncio.wait_for(reader.readline(), CONTROL_TIMEOUT)
                if not raw:
                    return
                if len(raw) > MAX_REQUEST_BYTES:
                    raise ValueError
                if not raw.endswith(b"\n"):
                    response = {"id": None, "ok": False, "error": "invalid_request"}
                else:
                    try:
                        request = ControlRequest.model_validate_json(raw)
                    except ValidationError:
                        response = {"id": None, "ok": False, "error": "invalid_request"}
                    else:
                        request_id = request.id
                        if request.method == "ping":
                            result = {"instance_id": self.status()["instance_id"]}
                        elif request.method == "status":
                            result = self.status()
                        else:
                            result = {"accepted": True}
                            stopping = True
                        response = {"id": request_id, "ok": True, "result": result}
            except ValueError:
                response = {"id": None, "ok": False, "error": "request_too_large"}
            except TimeoutError:
                response = {"id": None, "ok": False, "error": "request_timeout"}
            payload = json.dumps(response, ensure_ascii=True).encode("utf-8") + b"\n"
            if len(payload) > MAX_RESPONSE_BYTES:
                payload = json.dumps({"id": request_id, "ok": False, "error": "response_too_large"}).encode() + b"\n"
            writer.write(payload)
            await asyncio.wait_for(writer.drain(), CONTROL_TIMEOUT)
            # Deliver the acknowledgement before waking the shutdown coordinator.
            if stopping:
                self.stop()
        except (OSError, TimeoutError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never log the raw request, unknown values or configuration.
            logger.error("Local control handler failed")
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), CONTROL_TIMEOUT)
            except (OSError, TimeoutError):
                pass
            finally:
                self._connections.pop(task, None)
