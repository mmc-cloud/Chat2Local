"""`read` Tool: read text files and single-level directory listings.

Design: docs/design-v0.1.md §4.1 File System → read.

The Tool stays thin: every path goes through ``WorkspaceManager``, reading is
streaming and bounded by ``max_lines`` / ``max_bytes``, and truncation is
reported as a normal result instead of an error.

Truncation protocol: a file result carries ``truncation_reason`` = ``None``,
``line_limit``, ``byte_limit`` or ``line_too_long``; a directory result carries
``None``, ``byte_limit`` or ``scan_limit``. A single line larger than the whole
``max_bytes`` budget is returned as the UTF-8 prefix ending on a character
boundary, with ``truncation_reason = "line_too_long"`` and
``line_truncated = True``; the rest of that line cannot be paged through
``read`` in V0.1. Text decoding is strict — UTF-8, UTF-8 BOM and UTF-16 LE/BE
BOM are supported, any other content is reported as an error rather than
returned as mojibake.
"""

from __future__ import annotations

import asyncio
import codecs
import io
import json
import os
from pathlib import Path
from typing import Any

from chat2local.runtime.config import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES
from chat2local.runtime.workspace import WorkspaceManager

MAX_DIRECTORY_SCAN_ENTRIES = 10_000

LINE_LIMIT = "line_limit"
BYTE_LIMIT = "byte_limit"
LINE_TOO_LONG = "line_too_long"
SCAN_LIMIT = "scan_limit"

_SNIFF_BYTES = 8192


def detect_encoding(sample: bytes) -> str:
    """Return the text encoding of a file based on its BOM, defaulting to UTF-8."""

    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"

    if sample.startswith(codecs.BOM_UTF16_LE) or sample.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"

    return "utf-8"


def looks_binary(sample: bytes, encoding: str) -> bool:
    """A UTF-16 BOM already proves the file is text; otherwise a NUL byte means binary."""

    if encoding == "utf-16":
        return False

    return b"\x00" in sample


async def read(
    workspace: WorkspaceManager,
    path: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Read a text file (optionally a line range) or list one directory level.

    ``start_line`` / ``end_line`` are 1-based, ``end_line`` is inclusive.
    Reading stops at ``max_lines`` lines or ``max_bytes`` of text, whichever
    comes first, and reports the continuation line in ``next_start_line``.
    Binary files are reported without their content. Directories are listed one
    level only.
    """

    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be >= 1")

    if end_line is not None and end_line < 1:
        raise ValueError("end_line must be >= 1")

    if start_line is not None and end_line is not None and end_line < start_line:
        raise ValueError("end_line must be >= start_line")

    if max_lines < 1:
        raise ValueError("max_lines must be >= 1")

    if max_bytes < 1:
        raise ValueError("max_bytes must be >= 1")

    target = workspace.resolve_path(path)

    if target.is_dir():
        return await asyncio.to_thread(_read_directory, workspace, target, max_bytes)

    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")

    if not target.is_file():
        raise ValueError(f"Path is not a regular file: {path}")

    return await asyncio.to_thread(
        _read_file, workspace, target, start_line, end_line, max_lines, max_bytes
    )


def _read_file(
    workspace: WorkspaceManager,
    target: Path,
    start_line: int | None,
    end_line: int | None,
    max_lines: int,
    max_bytes: int,
) -> dict[str, Any]:
    relative = workspace.relative_path(target)
    first = start_line if start_line is not None else 1

    with target.open("rb") as handle:
        sample = handle.read(_SNIFF_BYTES)
        encoding = detect_encoding(sample)

        if looks_binary(sample, encoding):
            return {
                "kind": "binary",
                "path": relative,
                "size": target.stat().st_size,
                "message": "Binary file: content is not returned.",
                "truncation_reason": None,
            }

        handle.seek(0)

        with io.TextIOWrapper(handle, encoding=encoding, errors="strict", newline="") as text:
            chunk: list[str] = []
            used = 0
            lineno = 0
            first_returned: int | None = None
            last_returned: int | None = None
            truncated = False
            truncation_reason: str | None = None
            line_truncated = False
            reached_eof = False

            try:
                for lineno, line in enumerate(text, start=1):
                    if lineno < first:
                        continue

                    if end_line is not None and lineno > end_line:
                        break

                    if len(chunk) >= max_lines:
                        truncated = True
                        truncation_reason = LINE_LIMIT
                        break

                    size = len(line.encode("utf-8"))

                    if used + size > max_bytes:
                        truncated = True
                        remaining = max_bytes - used
                        prefix = _utf8_prefix(line, remaining)

                        if prefix and size > max_bytes:
                            # Part of this line is returned, so it is marked as a partial line;
                            # the rest of it cannot be paged through read in V0.1.
                            chunk.append(prefix)

                            if first_returned is None:
                                first_returned = lineno

                            last_returned = lineno
                            line_truncated = True
                            truncation_reason = LINE_TOO_LONG
                        else:
                            # Nothing of this line is returned — either no budget is left or the
                            # first character does not fit — so it stays unreturned and unmarked.
                            truncation_reason = BYTE_LIMIT

                        break

                    chunk.append(line)
                    used += size

                    if first_returned is None:
                        first_returned = lineno

                    last_returned = lineno
                else:
                    reached_eof = True
            except UnicodeDecodeError as error:
                raise ValueError(
                    f"{relative} is not valid {encoding} text: unsupported or invalid text encoding"
                ) from error

    result: dict[str, Any] = {
        "kind": "file",
        "path": relative,
        "encoding": encoding,
        "size": target.stat().st_size,
        "start_line": first_returned,
        "end_line": last_returned,
        "lines_returned": len(chunk),
        "text": "".join(chunk),
        "truncated": truncated,
        "truncation_reason": truncation_reason,
        "line_truncated": line_truncated,
        "next_start_line": None,
        "total_lines": lineno if reached_eof else None,
    }

    if truncated:
        result["next_start_line"] = (last_returned + 1) if line_truncated else lineno

    return result


def _read_directory(workspace: WorkspaceManager, target: Path, max_bytes: int) -> dict[str, Any]:
    names: list[str] = []
    scanned = 0
    scan_truncated = False

    with os.scandir(target) as entries:
        for entry in entries:
            scanned += 1

            if scanned > MAX_DIRECTORY_SCAN_ENTRIES:
                scan_truncated = True
                break

            names.append(entry.name)

    result: dict[str, Any] = {
        "kind": "directory",
        "path": workspace.relative_path(target),
        "entries": [],
        "entry_count": 0,
        "truncated": False,
        "truncation_reason": None,
    }

    for name in sorted(names):
        result["entries"].append(_directory_entry(target, name))

        # The budget applies to the entries payload only, not to the envelope metadata.
        if _payload_size(result["entries"]) > max_bytes:
            result["entries"].pop()
            result["truncated"] = True
            result["truncation_reason"] = BYTE_LIMIT
            break

    result["entry_count"] = len(result["entries"])

    if scan_truncated and not result["truncated"]:
        result["truncated"] = True
        result["truncation_reason"] = SCAN_LIMIT

    return result


def _directory_entry(target: Path, name: str) -> dict[str, Any]:
    child = target / name

    if child.is_symlink() or child.is_junction():
        kind = "symlink"
    elif child.is_dir():
        kind = "dir"
    elif child.is_file():
        kind = "file"
    else:
        kind = "other"

    entry: dict[str, Any] = {"name": name, "type": kind}

    if kind == "file":
        entry["size"] = child.lstat().st_size

    return entry


def _payload_size(value: Any) -> int:
    """Byte size of the compact JSON the caller receives, used as the directory budget."""

    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _utf8_prefix(line: str, budget: int) -> str:
    """Longest UTF-8 prefix of ``line`` that fits in ``budget`` bytes, cut on a character boundary."""

    return line.encode("utf-8")[: max(budget, 0)].decode("utf-8", "ignore")
