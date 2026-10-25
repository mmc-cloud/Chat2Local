"""Workspace and personal latest-state persistence in Chat2Local user data."""

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
from chat2local.handoff.paths import (
    MAX_WORKSPACE_KEY_BYTES, checked_path, personal_storage_directory,
    read_workspace_metadata, storage_directory,
)
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


def _validate_local_key(key: str) -> None:
    if (not key or len(key.encode("utf-8")) > MAX_WORKSPACE_KEY_BYTES
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', key) or key in (".", "..")
            or key.endswith((" ", "."))):
        raise InvalidHandoffError("invalid local Handoff identifier")


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
    """One store per dispatcher; workspace=None explicitly selects personal data.

    No cached business state or shutdown lifecycle.
    """

    def __init__(self, user_data: Path | None = None) -> None:
        self._locks: dict[tuple[Path | None, str], asyncio.Lock] = {}
        self._user_data = user_data if user_data is not None else config.user_data_directory()
        # Serialize resolution across workstreams so no worker observes a newly
        # created directory before its workspace.json ownership is published.
        self._directory_lock = threading.Lock()

    def _directory(self, workspace: WorkspaceManager | None, *, create: bool = False) -> Path:
        with self._directory_lock:
            if workspace is None:
                return personal_storage_directory(self._user_data, create=create)
            return storage_directory(workspace.root, self._user_data, create=create)

    async def list(self, workspace: WorkspaceManager | None) -> dict:
        return await asyncio.to_thread(self._list, workspace)

    def _list(self, workspace: WorkspaceManager | None) -> dict:
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

    async def get(self, workspace: WorkspaceManager | None, workstream: str) -> dict:
        validate_workstream(workstream)
        return await asyncio.to_thread(self._get, workspace, workstream)

    def _get(self, workspace: WorkspaceManager | None, workstream: str) -> dict:
        path = checked_path(self._directory(workspace), f"{workstream}.md")
        return asdict(_read(path, workstream))

    def _local_project(self, directory: Path) -> dict:
        _validate_local_key(directory.name)
        checked_path(directory)
        if not directory.is_dir():
            raise InvalidHandoffError("handoff storage must be a directory")
        if directory.name == "_personal":
            return {"key": directory.name, "scope": "personal", "name": "Personal", "path": None}
        metadata = read_workspace_metadata(directory)
        name = metadata.get("display_name")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise InvalidHandoffError("workspace.json display_name must be a nonblank string or null")
        owner = metadata["canonical_path"]
        return {"key": directory.name, "scope": "workspace", "name": name or Path(owner).name or owner,
                "path": owner}

    @staticmethod
    def _local_identity(project: str, workstream: str) -> str:
        # Encoding a single directory component keeps separators out of the ID.
        return f"{project.encode('utf-8').hex()}:{workstream}"

    async def list_local(self) -> dict:
        """Read all local scopes, isolating failures without caching or writing."""
        return await asyncio.to_thread(self._list_local)

    def _list_local(self) -> dict:
        result: dict = {"handoffs": [], "issues": []}

        def issue(location: str, error: Exception) -> None:
            result["issues"].append({"location": location, "message": str(error)})

        try:
            base = checked_path(self._user_data / "handoffs")
            if not base.exists():
                return result
            directories = sorted(base.iterdir(), key=lambda path: path.name)
        except (OSError, ValueError) as error:
            issue("handoffs", error)
            return result
        for directory in directories:
            try:
                # Reject aliases before filtering hidden management entries or files.
                checked_path(directory)
                if directory.name.startswith(".") or not directory.is_dir():
                    continue
                project = self._local_project(directory)
                entries = sorted(directory.iterdir(), key=lambda path: path.name)
            except (OSError, ValueError) as error:
                issue(directory.name, error)
                continue
            for entry in entries:
                if entry.suffix != ".md":
                    continue
                try:
                    validate_workstream(entry.stem)
                    record = _read(checked_path(directory, entry.name), entry.stem)
                    result["handoffs"].append({
                        "id": self._local_identity(directory.name, entry.stem), "project": project,
                        **asdict(HandoffMetadata(record.workstream, record.revision, record.updated_at,
                                                 record.title, record.summary)),
                    })
                except (OSError, ValueError) as error:
                    issue(f"{directory.name}/{entry.name}", error)
        return result

    async def get_local(self, handoff_id: str) -> dict:
        """Accept a validated scope/workstream identity, never a caller's path."""
        if not isinstance(handoff_id, str) or re.fullmatch(
            rf"[0-9a-f]{{2,{MAX_WORKSPACE_KEY_BYTES * 2}}}:[A-Za-z0-9][A-Za-z0-9_-]{{0,79}}",
            handoff_id,
        ) is None:
            raise InvalidHandoffError("invalid local Handoff identifier")
        encoded, workstream = handoff_id.split(":")
        try:
            key = bytes.fromhex(encoded).decode("utf-8")
        except (UnicodeError, ValueError) as error:
            raise InvalidHandoffError("invalid local Handoff identifier") from error
        _validate_local_key(key)
        return await asyncio.to_thread(self._get_local, key, workstream)

    def _get_local(self, key: str, workstream: str) -> dict:
        directory = checked_path(self._user_data / "handoffs", key)
        if not directory.exists():
            raise HandoffNotFoundError(workstream)
        project = self._local_project(directory)
        record = _read(checked_path(directory, f"{workstream}.md"), workstream)
        return {"id": self._local_identity(key, workstream), "project": project, **asdict(record)}

    async def save(
        self, workspace: WorkspaceManager | None, workstream: str, title: str, summary: str,
        content: str, expected_revision: int,
    ) -> dict:
        validate_workstream(workstream)
        validate_title(title)
        validate_summary(summary)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a strict integer >= 0")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        identity = (workspace.root if workspace is not None else None, os.path.normcase(workstream))
        lock = self._locks.setdefault(identity, asyncio.Lock())
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
        self, workspace: WorkspaceManager | None, workstream: str, title: str, summary: str,
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
