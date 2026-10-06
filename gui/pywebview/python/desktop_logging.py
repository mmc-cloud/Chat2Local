"""Desktop diagnostics without exception values, source code or frame locals."""

import copy
import logging
import sys
from contextlib import contextmanager
from pathlib import Path


def _exception_details(kind, traceback):
    frames = []
    while traceback is not None and len(frames) < 20:
        code = traceback.tb_frame.f_code
        frames.append(
            f"{Path(code.co_filename).name}:{traceback.tb_lineno}:{code.co_name}"
        )
        traceback = traceback.tb_next
    location = " -> ".join(frames) or "unavailable"
    return f"[{kind.__name__}]\ntraceback: {location}"


def log_exception_safe(logger, message, error):
    """Call with a fixed diagnostic. Never render error or its chained values."""
    logger.error("%s %s", message, _exception_details(type(error), error.__traceback__))


class DesktopSafeFormatter(logging.Formatter):
    """Also protect future exc_info calls and ignore cached unsafe tracebacks."""

    def format(self, record):
        safe_record = copy.copy(record)
        safe_record.exc_text = None
        safe_record.stack_info = None
        return super().format(safe_record)

    def formatException(self, exc_info):
        kind, _, traceback = exc_info
        return _exception_details(kind, traceback)


@contextmanager
def bootstrap_logging():
    """Ownership diagnostics use only safe stderr, never a persistent handler."""
    logger = logging.getLogger("chat2local.desktop")
    previous_handlers, previous_level, previous_propagate = (
        logger.handlers,
        logger.level,
        logger.propagate,
    )
    handler = (
        logging.StreamHandler(sys.stderr)
        if sys.stderr is not None
        else logging.NullHandler()
    )
    handler.setFormatter(DesktopSafeFormatter("%(levelname)s %(name)s: %(message)s"))
    logger.handlers = [handler]
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    try:
        yield
    finally:
        logger.handlers = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
        handler.close()
