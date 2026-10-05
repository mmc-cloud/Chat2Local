"""Bounded incremental reads of the existing rotating Core log."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from chat2local.management.runtime import ManagementError

MAX_LOG_BYTES = 256 * 1024
MAX_TAIL_LINES = 1000


class LogCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    identity: str = Field(max_length=128)
    offset: int = Field(ge=0)
    prefix_size: int = Field(ge=0, le=64)
    signature: str = Field(max_length=64)


class LogReader:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self, cursor: dict | None = None) -> dict:
        previous = LogCursor.model_validate(cursor) if cursor is not None else None
        try:
            with self.path.open("rb") as handle:
                import os

                stat = os.fstat(handle.fileno())
                identity = f"{stat.st_dev}:{stat.st_ino}"
                reset = (
                    previous is None
                    or previous.identity != identity
                    or stat.st_size < previous.offset
                )
                if previous and not reset:
                    prefix = handle.read(previous.prefix_size)
                    reset = hashlib.sha256(prefix).hexdigest() != previous.signature
                start = (
                    max(0, stat.st_size - MAX_LOG_BYTES) if reset else previous.offset
                )
                handle.seek(start)
                data = handle.read(MAX_LOG_BYTES)
                if reset and start:
                    newline = data.find(b"\n")
                    if newline >= 0:
                        start += newline + 1
                        data = data[newline + 1 :]
                end = data.rfind(b"\n") + 1
                if end == 0 and len(data) == MAX_LOG_BYTES:
                    # Bound pathological long lines; preserve a split UTF-8 scalar.
                    import codecs

                    decoder = codecs.getincrementaldecoder("utf-8")("replace")
                    text = decoder.decode(data, final=False)
                    end = len(data) - len(decoder.getstate()[0])
                    lines = [text]
                else:
                    lines = data[:end].decode("utf-8", errors="replace").splitlines()
                prefix_size = min(64, stat.st_size)
                handle.seek(0)
                signature = hashlib.sha256(handle.read(prefix_size)).hexdigest()
                position = LogCursor(
                    identity=identity,
                    offset=start + end,
                    prefix_size=prefix_size,
                    signature=signature,
                )
                return {
                    "lines": lines[-MAX_TAIL_LINES:] if reset else lines,
                    "cursor": position.model_dump(),
                    "reset": reset,
                    "missing": False,
                }
        except FileNotFoundError:
            return {
                "lines": [],
                "cursor": None,
                "reset": previous is not None,
                "missing": True,
            }
        except OSError:
            raise ManagementError("Could not read log file") from None
