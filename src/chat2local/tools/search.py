"""`search` Tool: search file names or file contents inside the workspace.

Design: docs/design-v0.1.md §4.1 File System → search.

ripgrep is preferred when available; otherwise a Python fallback walks the
workspace with the same default excludes. Timeout and ``max_results`` are
normal truncation, not Tool errors.

Truncation protocol: a result carries ``truncation_reason`` = ``None``,
``max_results``, ``timeout`` or ``output_limit``; whichever condition stopped
the search first wins. ``timed_out`` stays as the explicit timeout flag and is
true only for ``timeout``.

Only the fixed directory excludes are pushed into ripgrep. User ``include`` /
``exclude`` globs are never handed to a backend: both backends narrow their
candidates through the same ``_passes_filters``, so one set of glob semantics
applies no matter which engine ran.
"""

from __future__ import annotations

import asyncio
import fnmatch
import io
import json
import os
import posixpath
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from chat2local.runtime.config import (
    DEFAULT_MAX_RESULTS,
    DEFAULT_SEARCH_MATCH_TEXT_CHARS,
    DEFAULT_SEARCH_RESULTS_BYTES,
    DEFAULT_TIMEOUT,
    HARD_MAX_RESULTS,
)
from chat2local.runtime.workspace import WorkspaceManager
from chat2local.tools.read import detect_encoding, looks_binary

MAX_RESULTS = "max_results"
TIMEOUT = "timeout"
OUTPUT_LIMIT = "output_limit"

_EXIT_TIMEOUT = 5.0
_READ_CHUNK_BYTES = 64 * 1024

DEFAULT_EXCLUDES = ("node_modules", ".venv", "__pycache__", "dist", "build", "coverage")
ALWAYS_EXCLUDED_DIRS = (".git",)
IGNORE_FILES = (".gitignore", ".ignore", ".rgignore")

_SNIFF_BYTES = 8192


async def search(
    workspace: WorkspaceManager,
    query: str,
    *,
    mode: str,
    path: str = ".",
    include: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
    regex: bool = False,
    case_sensitive: bool = False,
    max_results: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Search file names (``mode="name"``) or file contents (``mode="content"``).

    Results are workspace-relative paths. ``max_results`` defaults to 100 and is
    capped at 500. ``timeout``, the result cap and the result payload budget
    truncate the search normally and report why in ``truncation_reason``.
    """

    if mode not in ("name", "content"):
        raise ValueError("mode must be 'name' or 'content'")

    if not query:
        raise ValueError("query must not be empty")

    limit = DEFAULT_MAX_RESULTS if max_results is None else max_results

    if limit < 1:
        raise ValueError("max_results must be >= 1")

    if timeout <= 0:
        raise ValueError("timeout must be > 0")

    limit = min(limit, HARD_MAX_RESULTS)

    base = workspace.resolve_path(path)

    if not base.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")

    matcher = _compile(query, regex, case_sensitive)
    base_relative = workspace.relative_path(base)
    has_separator = "/" in query

    # A search base inside an always-excluded directory would be walked directly,
    # sidestepping the per-entry excludes, so it answers from the fixed list instead.
    if _is_fixed_excluded_path(base_relative):
        return {
            "mode": mode,
            "query": query,
            "path": base_relative,
            "engine": "ripgrep" if shutil.which("rg") is not None else "python",
            "results": [],
            "result_count": 0,
            "truncated": False,
            "truncation_reason": None,
            "timed_out": False,
        }

    ripgrep = shutil.which("rg")

    if ripgrep is not None:
        results, truncation_reason = await _ripgrep_search(
            ripgrep=ripgrep,
            root=workspace.root,
            base_relative=base_relative,
            query=query,
            mode=mode,
            regex=regex,
            case_sensitive=case_sensitive,
            include=include,
            exclude=exclude,
            matcher=matcher,
            has_separator=has_separator,
            limit=limit,
            timeout=timeout,
        )
        engine = "ripgrep"
    else:
        results, truncation_reason = await asyncio.to_thread(
            _python_search,
            root=workspace.root,
            base=base,
            mode=mode,
            include=include,
            exclude=exclude,
            matcher=matcher,
            has_separator=has_separator,
            limit=limit,
            timeout=timeout,
        )
        engine = "python"

    return {
        "mode": mode,
        "query": query,
        "path": base_relative,
        "engine": engine,
        "results": results,
        "result_count": len(results),
        "truncated": truncation_reason is not None,
        "truncation_reason": truncation_reason,
        "timed_out": truncation_reason == TIMEOUT,
    }


# --- shared matching helpers -------------------------------------------------


def _compile(query: str, regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = query if regex else re.escape(query)

    try:
        return re.compile(pattern, flags)
    except re.error as error:
        raise ValueError(f"Invalid pattern: {error}") from error


def _glob_match(pattern: str, relative: str, name: str) -> bool:
    if "/" in pattern:
        return fnmatch.fnmatchcase(relative, pattern)

    return fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(relative, pattern)


def _passes_filters(
    relative: str,
    name: str,
    include: Sequence[str] | None,
    exclude: Sequence[str] | None,
) -> bool:
    if include and not any(_glob_match(pattern, relative, name) for pattern in include):
        return False

    if exclude and any(_glob_match(pattern, relative, name) for pattern in exclude):
        return False

    return True


def _match_name(
    matcher: re.Pattern[str],
    relative: str,
    name: str,
    has_separator: bool,
) -> bool:
    return matcher.search(relative if has_separator else name) is not None


def _normalize(printed_path: str) -> str:
    return posixpath.normpath(printed_path.replace("\\", "/"))


def _clamp_text(
    text: str,
    matcher: re.Pattern[str],
    limit: int = DEFAULT_SEARCH_MATCH_TEXT_CHARS,
) -> tuple[str, bool]:
    """Cut a long match line down to a window of at most ``limit`` characters.

    The matcher decides where the window goes, so a match far into a long line
    survives the cut. No filler is inserted — the caller gets source text only,
    and ``True`` as the second element when anything was dropped.
    """

    if len(text) <= limit:
        return text, False

    match = matcher.search(text)

    if match is None:
        return text[:limit], True

    start, end = match.span()

    if end - start >= limit:
        return text[start : start + limit], True

    # Centre the match, then slide the window back inside the line.
    offset = start - (limit - (end - start)) // 2
    offset = max(0, min(offset, len(text) - limit))

    return text[offset : offset + limit], True


def _payload_size(value: Any) -> int:
    """Byte size of the compact JSON the caller receives, used as the results budget."""

    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _cap_reason(
    results: list[dict[str, Any]],
    item: dict[str, Any],
    limit: int,
) -> str | None:
    """The cap that would stop ``item`` being added to ``results``, if any.

    Pure: the caller decides whether to append, which lets the ripgrep backend
    vet matches it is holding before it buffers them.
    """

    if len(results) >= limit:
        return MAX_RESULTS

    if _payload_size([*results, item]) > DEFAULT_SEARCH_RESULTS_BYTES:
        return OUTPUT_LIMIT

    return None


def _commit(results: list[dict[str, Any]], item: dict[str, Any], limit: int) -> str | None:
    """Append ``item`` unless it would breach a cap; report the cap that stopped it.

    ``None`` means the item was committed, so a truthy return is always a
    truncation reason.
    """

    reason = _cap_reason(results, item, limit)

    if reason is None:
        results.append(item)

    return reason


class _SearchTimedOut(Exception):
    """Raised by the fallback walk when the deadline passes, so nothing can swallow it."""


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise _SearchTimedOut


def _is_fixed_excluded_path(relative: str) -> bool:
    """True when a path runs through a directory the Tool always excludes."""

    excluded = {*ALWAYS_EXCLUDED_DIRS, *DEFAULT_EXCLUDES}

    return any(part in excluded for part in PurePosixPath(relative).parts)


def _is_link_like(path: Path) -> bool:
    """True for symlinks and, on Windows, for junctions and other reparse points."""

    if path.is_symlink():
        return True

    if os.name != "nt":
        return False

    try:
        attributes = path.lstat().st_file_attributes
    except OSError:
        return False

    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _ancestor_chain(root: Path, base: Path) -> list[Path]:
    """Directories from the workspace root down to the parent of ``base``."""

    if base == root:
        return []

    chain: list[Path] = []
    current = base.parent

    while current != root:
        if current == current.parent:
            # base is not inside root, defensively load nothing
            return []

        chain.append(current)
        current = current.parent

    chain.append(root)
    chain.reverse()

    return chain


# --- ripgrep backend ---------------------------------------------------------


def _ripgrep_globs() -> list[str]:
    """Excludes ripgrep may apply itself; user globs are never pushed down."""

    args: list[str] = []

    for name in (*ALWAYS_EXCLUDED_DIRS, *DEFAULT_EXCLUDES):
        args += ["--glob", f"!{name}", "--glob", f"!{name}/**"]

    return args


async def _ripgrep_search(
    *,
    ripgrep: str,
    root: Path,
    base_relative: str,
    query: str,
    mode: str,
    regex: bool,
    case_sensitive: bool,
    include: Sequence[str] | None,
    exclude: Sequence[str] | None,
    matcher: re.Pattern[str],
    has_separator: bool,
    limit: int,
    timeout: float,
) -> tuple[list[dict[str, Any]], str | None]:
    args = [
        ripgrep,
        "--no-config",
        "--no-require-git",
        "--hidden",
        "--sort",
        "path",
        "--path-separator",
        "/",
        *_ripgrep_globs(),
    ]

    if mode == "name":
        args.append("--files")
    else:
        args.append("--json")
        args.append("--case-sensitive" if case_sensitive else "--ignore-case")

        if not regex:
            args.append("--fixed-strings")

    args.append("--")

    if mode == "content":
        args.append(query)

    args.append(base_relative)

    def name_item(line: str) -> dict[str, Any] | None:
        relative = _normalize(line)
        name = PurePosixPath(relative).name

        if not _passes_filters(relative, name, include, exclude):
            return None

        if not _match_name(matcher, relative, name, has_separator):
            return None

        return {"path": relative, "name": name, "type": "file"}

    def match_item(event: dict[str, Any]) -> dict[str, Any] | None:
        data = event.get("data") or {}
        path_text = (data.get("path") or {}).get("text")
        line_text = (data.get("lines") or {}).get("text")

        # Binary lines arrive as ``lines.bytes`` and a path outside UTF-8 as
        # ``path.bytes``; neither can be matched against the decoded query.
        if path_text is None or line_text is None:
            return None

        relative = _normalize(path_text)
        name = PurePosixPath(relative).name

        if not _passes_filters(relative, name, include, exclude):
            return None

        clipped, was_clipped = _clamp_text(line_text.rstrip("\r\n"), matcher)

        item: dict[str, Any] = {
            "path": relative,
            "line": data.get("line_number"),
            "text": clipped,
        }

        if was_clipped:
            item["text_truncated"] = True

        return item

    def event_binary(event: dict[str, Any]) -> bool | None:
        """True/False once a file's ``end`` event arrives, ``None`` before that."""

        if event.get("type") != "end":
            return None

        data = event.get("data") or {}

        return data.get("binary_offset") is not None

    results: list[dict[str, Any]] = []
    reason: str | None = None
    pending: list[dict[str, Any]] = []
    pending_reason: str | None = None
    deadline = time.monotonic() + timeout
    def drain() -> str | None:
        """Commit a finished text file's matches; the cap that stopped us, if any."""

        while pending:
            stopped = _commit(results, pending.pop(0), limit)

            if stopped is not None:
                return stopped

        return None


    with tempfile.TemporaryFile() as error_log:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=error_log,
        )

        try:
            # Read fixed-size chunks instead of readline(): a single match line can be
            # far larger than the StreamReader buffer limit, and readline() raises
            # ValueError when no separator shows up within that limit.
            rest = b""

            while True:
                if b"\n" in rest:
                    raw, rest = rest.split(b"\n", 1)
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")

                    if mode == "name":
                        item = name_item(line) if line else None

                        if item is not None:
                            reason = _commit(results, item, limit)

                            if reason is not None:
                                rest = b""
                                break

                        continue

                    event = _parse_event(line)

                    if event is None:
                        continue

                    if event.get("type") == "match":
                        item = match_item(event)

                        # Vet the match against the caps before buffering it, so
                        # one file cannot pile up matches the search can never
                        # return. Reading continues to that file's end event,
                        # which is the only thing that can prove it is not binary.
                        if item is not None and pending_reason is None:
                            pending_reason = _cap_reason([*results, *pending], item, limit)

                            if pending_reason is None:
                                pending.append(item)

                        continue

                    # A file is only known to be text once its end event says so,
                    # so nothing from it is committed before then.
                    is_binary = event_binary(event)

                    if is_binary is None:
                        continue

                    # A binary file contributes nothing, so its buffered matches are
                    # dropped along with the reason they hit a cap: a file the search
                    # never returns must not truncate it.
                    if is_binary:
                        pending.clear()
                        pending_reason = None

                        continue

                    # Committing the buffer comes first: the matches it holds are
                    # within the caps and are results the caller asked for. A cap hit
                    # while buffering only stops the search after them.
                    stopped = drain()

                    if stopped is None:
                        stopped = pending_reason

                    pending.clear()

                    if stopped is not None:
                        reason = stopped
                        rest = b""
                        break

                    continue

                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    reason = TIMEOUT
                    break

                try:
                    chunk = await asyncio.wait_for(
                        process.stdout.read(_READ_CHUNK_BYTES), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    reason = TIMEOUT
                    break

                if not chunk:
                    if rest:
                        # close the final line so it takes the same path as the others
                        rest += b"\n"
                        continue

                    break

                rest += chunk
        finally:
            # stdout reaching EOF does not mean the child has been reaped yet, so only
            # kill a process we actually stopped reading early.
            if process.returncode is None and reason is not None:
                _terminate(process)

            try:
                await asyncio.wait_for(process.wait(), timeout=_EXIT_TIMEOUT)
            except asyncio.TimeoutError:
                _terminate(process)
                await process.wait()

            exit_code = process.returncode
            error_log.seek(0)
            stderr = error_log.read().decode("utf-8", "replace").strip()

    # Matches left in ``pending`` belong to a file whose end event never arrived;
    # nothing proves that file is not binary, so they are dropped.
    if reason is None and exit_code not in (0, 1):
        raise RuntimeError(f"ripgrep failed: {stderr or exit_code}")

    return results, reason


def _terminate(process: asyncio.subprocess.Process) -> None:
    """Kill a ripgrep process, tolerating one that already exited."""

    try:
        process.kill()
    except OSError:
        pass


def _parse_event(line: str) -> dict[str, Any] | None:
    """Decode one ripgrep JSON line, ignoring anything that is not an event object."""

    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None

    if not isinstance(event, dict) or "type" not in event:
        return None

    return event


# --- Python fallback backend -------------------------------------------------


def _python_search(
    *,
    root: Path,
    base: Path,
    mode: str,
    include: Sequence[str] | None,
    exclude: Sequence[str] | None,
    matcher: re.Pattern[str],
    has_separator: bool,
    limit: int,
    timeout: float,
) -> tuple[list[dict[str, Any]], str | None]:
    results: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout

    try:
        for candidate in _iter_files(root, base, deadline):
            relative = candidate.relative_to(root).as_posix()
            name = candidate.name

            if not _passes_filters(relative, name, include, exclude):
                continue

            if mode == "name":
                if not _match_name(matcher, relative, name, has_separator):
                    continue

                reason = _commit(results, {"path": relative, "name": name, "type": "file"}, limit)

                if reason is not None:
                    return results, reason

                continue

            for line_number, text in _matching_lines(candidate, matcher, deadline):
                clipped, was_clipped = _clamp_text(text, matcher)
                item: dict[str, Any] = {"path": relative, "line": line_number, "text": clipped}

                if was_clipped:
                    item["text_truncated"] = True

                reason = _commit(results, item, limit)

                if reason is not None:
                    return results, reason
    except _SearchTimedOut:
        return results, TIMEOUT

    return results, None


def _iter_files(root: Path, base: Path, deadline: float):
    """Yield searchable files below ``base``; symlinks and junctions are never followed.

    Raises ``_SearchTimedOut`` once the deadline passes rather than stopping, so a
    timeout anywhere in the walk reaches the caller as a truncation reason.
    """

    _check_deadline(deadline)

    if base.is_file():
        yield base
        return

    ignore = _IgnoreRules()
    loaded: list[Path] = []

    # Ripgrep applies the ignore files from the workspace root down to the search base,
    # so the fallback has to load that same ancestry before walking the base.
    for directory in _ancestor_chain(root, base):
        _check_deadline(deadline)
        ignore.load(directory)
        loaded.append(directory)

    for current, dirnames, filenames in os.walk(base, followlinks=False):
        _check_deadline(deadline)

        directory = Path(current)

        while loaded and loaded[-1] != directory.parent:
            ignore.unload()
            loaded.pop()

        ignore.load(directory)
        loaded.append(directory)

        kept: list[str] = []

        for name in dirnames:
            child = directory / name

            if name in ALWAYS_EXCLUDED_DIRS or name in DEFAULT_EXCLUDES:
                continue

            if ignore.ignored(child, is_dir=True):
                continue

            if _is_link_like(child):
                continue

            kept.append(name)

        # keep traversal order deterministic across filesystems
        dirnames[:] = sorted(kept)

        for name in sorted(filenames):
            child = directory / name

            if ignore.ignored(child, is_dir=False):
                continue

            if _is_link_like(child):
                continue

            yield child


def _matching_lines(candidate: Path, matcher: re.Pattern[str], deadline: float):
    try:
        handle = candidate.open("rb")
    except OSError:
        return

    with handle:
        sample = handle.read(_SNIFF_BYTES)
        encoding = detect_encoding(sample)

        if looks_binary(sample, encoding):
            return

        handle.seek(0)

        # Strict decoding: a file that is not valid text is skipped instead of
        # being searched with replacement characters spliced into its lines.
        try:
            with io.TextIOWrapper(
                handle, encoding=encoding, errors="strict", newline=""
            ) as text:
                for line_number, line in enumerate(text, start=1):
                    _check_deadline(deadline)

                    if matcher.search(line):
                        yield line_number, line.rstrip("\r\n")
        except UnicodeDecodeError:
            return


class _IgnoreRules:
    """Minimal `.gitignore` / `.ignore` / `.rgignore` support for the fallback backend.

    Patterns follow gitignore basics: `!` negation, trailing `/` for directories
    and a leading `/` to anchor to the directory holding the ignore file.
    """

    def __init__(self) -> None:
        self._levels: list[tuple[Path, list[tuple[str, bool, bool, bool]]]] = []

    def load(self, directory: Path) -> None:
        patterns: list[tuple[str, bool, bool, bool]] = []

        for name in IGNORE_FILES:
            source = directory / name

            try:
                lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            for raw in lines:
                parsed = _parse_ignore_line(raw)

                if parsed is not None:
                    patterns.append(parsed)

        self._levels.append((directory, patterns))

    def unload(self) -> None:
        if self._levels:
            self._levels.pop()

    def ignored(self, path: Path, is_dir: bool) -> bool:
        decision = False

        for directory, patterns in self._levels:
            try:
                relative = path.relative_to(directory).as_posix()
            except ValueError:
                continue

            for pattern, negated, dir_only, anchored in patterns:
                if dir_only and not is_dir:
                    continue

                if anchored:
                    if fnmatch.fnmatchcase(relative, pattern) or relative.startswith(f"{pattern}/"):
                        decision = not negated
                elif _glob_match(pattern, relative, path.name):
                    decision = not negated

        return decision


def _parse_ignore_line(raw: str) -> tuple[str, bool, bool, bool] | None:
    line = raw.strip()

    if not line or line.startswith("#"):
        return None

    negated = line.startswith("!")

    if negated:
        line = line[1:]

    dir_only = line.endswith("/")
    line = line.rstrip("/")
    anchored = line.startswith("/")
    line = line.lstrip("/")

    if not line:
        return None

    return line, negated, dir_only, anchored
