"""Workspace-scoped latest-state persistence in Chat2Local user data."""

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import tempfile
import threading

from chat2local.handoff.models import (
    HandoffMetadata, HandoffNotFoundError, HandoffRecord, InvalidHandoffError,
    InvalidWorkstreamError, RevisionConflictError,
)
from chat2local.handoff.paths import checked_path, storage_directory
from chat2local.runtime import config
from chat2local.runtime.workspace import WorkspaceManager

WORKSTREAM_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$"
TITLE_MAX_LENGTH = 120
SUMMARY_MAX_LENGTH = 500
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)")


def validate_workstream(workstream: str) -> str:
    if not isinstance(workstream, str) or re.fullmatch(WORKSTREAM_PATTERN, workstream) is None:
        raise InvalidWorkstreamError("use 1-80 ASCII letters, digits, '-' or '_'; start with a letter or digit")
    return workstream


def _validate_single_line(value: str, field: str, max_length: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= max_length:
        raise ValueError(f"{field} must be a strict string of 1-{max_length} characters")
    if "\r" in value or "\n" in value or not value.strip():
        raise ValueError(f"{field} must be a single nonblank line")
    return value


def validate_title(value: str) -> str:
    return _validate_single_line(value, "title", TITLE_MAX_LENGTH)


def validate_summary(value: str) -> str:
    return _validate_single_line(value, "summary", SUMMARY_MAX_LENGTH)


def _read(path: Path, workstream: str) -> HandoffRecord:
    if not path.exists():
        raise HandoffNotFoundError(workstream)
    if not path.is_file():
        raise InvalidHandoffError(f"{workstream}: expected a regular file")
    try:
        text = path.read_bytes().decode("utf-8")
        # A fixed header avoids interpreting user Markdown as YAML metadata.
        header = re.fullmatch(
            r"---\nrevision: ([1-9][0-9]*)\nupdated_at: ([^\n]+)\n"
            r"title: ([^\n]*)\nsummary: ([^\n]*)\n---\n\n([\s\S]*)", text,
        )
        if header is None:
            raise ValueError("expected revision/updated_at/title/summary header")
        revision, updated_at, title, summary, content = header.groups()
        if _TIMESTAMP.fullmatch(updated_at) is None:
            raise ValueError("updated_at must be a UTC RFC3339 timestamp")
        datetime.fromisoformat(updated_at)
        validate_title(title)
        validate_summary(summary)
        return HandoffRecord(workstream, int(revision), updated_at, title, summary, content)
    except (UnicodeError, ValueError) as error:
        raise InvalidHandoffError(f"{workstream}: {error}") from error


class HandoffStore:
    """One store per dispatcher; no cached business state or shutdown lifecycle."""

    def __init__(self, user_data: Path | None = None) -> None:
        self._locks: dict[Path, asyncio.Lock] = {}
        self._user_data = user_data if user_data is not None else config.user_data_directory()
        # Serialize resolution across workstreams so no worker observes a newly
        # created directory before its workspace.json ownership is published.
        self._directory_lock = threading.Lock()

    def _directory(self, workspace: WorkspaceManager, *, create: bool = False) -> Path:
        with self._directory_lock:
            return storage_directory(workspace.root, self._user_data, create=create)

    async def list(self, workspace: WorkspaceManager) -> dict:
        return await asyncio.to_thread(self._list, workspace)

    def _list(self, workspace: WorkspaceManager) -> dict:
        directory = self._directory(workspace)
        if not directory.exists():
            return {"handoffs": []}
        if not directory.is_dir():
            raise InvalidHandoffError("handoff storage must be a directory")
        records = []
        for entry in sorted(directory.iterdir(), key=lambda path: path.name):
            if entry.suffix != ".md":
                continue
            try:
                validate_workstream(entry.stem)
            except InvalidWorkstreamError as error:
                raise InvalidHandoffError(f"invalid workstream filename: {entry.name}") from error
            path = checked_path(directory, entry.name)
            if path.is_dir():
                continue
            record = _read(path, entry.stem)
            records.append(asdict(HandoffMetadata(
                record.workstream, record.revision, record.updated_at, record.title, record.summary,
            )))
        return {"handoffs": records}

    async def get(self, workspace: WorkspaceManager, workstream: str) -> dict:
        validate_workstream(workstream)
        return await asyncio.to_thread(self._get, workspace, workstream)

    def _get(self, workspace: WorkspaceManager, workstream: str) -> dict:
        path = checked_path(self._directory(workspace), f"{workstream}.md")
        return asdict(_read(path, workstream))

    async def save(
        self, workspace: WorkspaceManager, workstream: str, title: str, summary: str,
        content: str, expected_revision: int,
    ) -> dict:
        validate_workstream(workstream)
        validate_title(title)
        validate_summary(summary)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a strict integer >= 0")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        lock = self._locks.setdefault(workspace.root / f"{workstream}.md", asyncio.Lock())
        async with lock:
            worker = asyncio.create_task(asyncio.to_thread(
                self._save, workspace, workstream, title, summary, content, expected_revision,
            ))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                # A cancelled request cannot release the lock while its OS write
                # still runs, including across an Agent disconnect/reconnect.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not worker.cancelled():
                    worker.exception()  # Retrieve a failed worker's exception.
                raise

    def _save(
        self, workspace: WorkspaceManager, workstream: str, title: str, summary: str,
        content: str, expected_revision: int,
    ) -> dict:
        directory = self._directory(workspace)
        path = checked_path(directory, f"{workstream}.md")
        current = _read(path, workstream).revision if path.exists() else 0
        if current != expected_revision:
            raise RevisionConflictError(expected_revision, current)
        if not directory.exists():
            directory = self._directory(workspace, create=True)
            path = checked_path(directory, f"{workstream}.md")
            # Ownership may have been claimed between lookup and creation.
            current = _read(path, workstream).revision if path.exists() else 0
            if current != expected_revision:
                raise RevisionConflictError(expected_revision, current)
        metadata = HandoffMetadata(
            workstream, current + 1,
            datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            title, summary,
        )
        body = content.replace("\r\n", "\n").replace("\r", "\n")
        text = (
            f"---\nrevision: {metadata.revision}\nupdated_at: {metadata.updated_at}\n"
            f"title: {title}\nsummary: {summary}\n---\n\n{body}"
        )
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=directory,
                prefix=".handoff-", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(text)
                handle.flush()
            os.replace(temporary, checked_path(directory, f"{workstream}.md"))
        except (OSError, UnicodeError) as error:
            raise OSError(f"handoff_write_failed: {workstream}: {error}") from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # Best effort cleanup; never disguise a write failure.
        return asdict(metadata)
