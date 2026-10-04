"""Small stdlib logging setup and one narrowly identified WebSocket callback noise."""

import asyncio
from contextlib import contextmanager
import logging
import re

from websockets.asyncio.client import ClientConnection
from websockets.asyncio.connection import Connection

logger = logging.getLogger(__name__)


class SafeFormatter(logging.Formatter):
    """Redact known connection credentials, including those in exception traces."""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__("%(levelname)s %(name)s: %(message)s")
        self.secrets = tuple(sorted({value for value in secrets if value}, key=len, reverse=True))

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        # Also protect credentials in URLs which weren't configured by this process.
        text = re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/@]+@", r"\1[redacted]@", text)
        for secret in self.secrets:
            text = text.replace(secret, "[redacted]")
        return text


def configure_logging(*, debug: bool = False, secrets: tuple[str, ...] = ()) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter(secrets))
    # The CLI owns logging setup; replace any preinstalled unsafe console handlers.
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("chat2local").setLevel(logging.DEBUG if debug else logging.INFO)
    # Dependency DEBUG messages can include authentication frames or request bodies.
    for name in ("websockets", "mcp", "httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _is_recv_messages_noise(error: BaseException | None) -> bool:
    if type(error) is not AttributeError or error.name != "recv_messages":
        return False
    connection = error.obj
    if type(connection) is not ClientConnection or "recv_messages" in vars(connection):
        return False
    trace = error.__traceback__
    if trace is None:
        return False
    while trace.tb_next is not None:
        trace = trace.tb_next
    return (trace.tb_frame.f_code is Connection.connection_lost.__code__
            and trace.tb_frame.f_locals.get("self") is connection)


@contextmanager
def websocket_callback_noise(loop: asyncio.AbstractEventLoop):
    """Scope the filter to an Agent runner; preserve existing/default handlers."""
    previous = loop.get_exception_handler()

    def handle(current_loop, context):
        if context.get("handle") is not None and _is_recv_messages_noise(context.get("exception")):
            logger.debug("Known websockets connection_lost callback noise (missing recv_messages)")
        elif previous is not None:
            previous(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(handle)
    try:
        yield
    finally:
        if loop.get_exception_handler() is handle:
            loop.set_exception_handler(previous)
