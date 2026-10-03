"""Strict workspace-bounded patches: validate a complete plan, then commit it.

No network state, retries, journal or rollback. Commit errors report completed
steps (including an Update completed before its subsequent Move fails).
"""

from __future__ import annotations

import asyncio
import codecs
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any

from chat2local.runtime.workspace import WorkspaceError, WorkspaceManager


class PatchError(ValueError):
    """A patch cannot be prepared; nothing has been written."""


@dataclass(frozen=True)
class Hunk:
    lines: tuple[str, ...]
    anchor: str | None = None
    at_end: bool = False


@dataclass(frozen=True)
class Operation:
    action: str
    path: str
    destination: str | None = None
    lines: tuple[str, ...] = ()
    hunks: tuple[Hunk, ...] = ()


def parse_patch(patch: str) -> tuple[Operation, ...]:
    if not isinstance(patch, str) or "\x00" in patch:
        raise PatchError("Patch must be text without NUL characters")
    lines = patch.replace("\r\n", "\n").split("\n")
    if any("\r" in line for line in lines):
        raise PatchError("Patch must use LF or CRLF line endings")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise PatchError("Expected *** Begin Patch and *** End Patch")
    operations: list[Operation] = []
    index = 1
    headers = {
        "*** Add File: ": "add", "*** Update File: ": "update",
        "*** Delete File: ": "delete", "*** Move Directory: ": "move_directory",
    }
    while index < len(lines) - 1:
        if lines[index] == "":
            index += 1
            continue
        header = lines[index]
        prefix = next((value for value in headers if header.startswith(value)), None)
        if prefix is None:
            raise PatchError(f"Unsupported patch header at line {index + 1}")
        action, path = headers[prefix], header[len(prefix):]
        _validate_path_text(path)
        index += 1
        destination = None
        content: list[str] = []
        hunks: list[Hunk] = []
        while index < len(lines) - 1:
            line = lines[index]
            if any(line.startswith(value) for value in headers):
                break
            if line == "":
                index += 1
                continue
            if line.startswith("*** Move to: "):
                if action not in ("update", "move_directory") or destination is not None:
                    raise PatchError("Move to requires one Update File or Move Directory")
                destination = line[len("*** Move to: "):]
                _validate_path_text(destination)
                index += 1
                continue
            if action == "add" and line.startswith("+"):
                content.append(line[1:])
                index += 1
                continue
            if action == "update" and (line == "@@" or line.startswith("@@ ")):
                if hunks and hunks[-1].at_end:
                    raise PatchError("End of File must end the last hunk")
                anchor = None if line == "@@" else line[3:]
                chunk: list[str] = []
                index += 1
                while index < len(lines) - 1 and lines[index][:1] in (" ", "+", "-"):
                    chunk.append(lines[index])
                    index += 1
                at_end = index < len(lines) - 1 and lines[index] == "*** End of File"
                if at_end:
                    index += 1
                if not chunk or not any(value.startswith(("+", "-")) for value in chunk):
                    raise PatchError("Update hunk must contain a change")
                hunks.append(Hunk(tuple(chunk), anchor, at_end))
                continue
            raise PatchError(f"Invalid {action} body at line {index + 1}")
        if action == "move_directory" and destination is None:
            raise PatchError("Move Directory requires Move to")
        if action == "update" and not hunks and destination is None:
            raise PatchError("Update File requires a hunk or Move to")
        operations.append(Operation(action, path, destination, tuple(content), tuple(hunks)))
    if not operations:
        raise PatchError("Patch contains no operations")
    return tuple(operations)


def _validate_path_text(path: str) -> None:
    if not path or path != path.strip() or "\r" in path:
        raise PatchError("Patch paths must be nonempty with no surrounding whitespace")


def _resolve(workspace: WorkspaceManager, path: str | Path) -> Path:
    resolved = workspace.resolve_path(path)
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = workspace.root / raw
    if os.name == "nt" and ":" in str(raw)[len(raw.drive):]:
        raise PatchError("NTFS alternate data streams are unsupported")
    if os.name == "nt" and any(
        character in '<>"|?*' or ord(character) < 32
        for part in raw.parts[1:] for character in part
    ):
        raise PatchError("Invalid Windows path characters")
    if os.name == "nt" and any(
        part.endswith((".", " ")) or Path(part).is_reserved() for part in raw.parts[1:]
        if part not in (".", "..")
    ):
        raise PatchError("Reserved or aliased Windows paths are unsupported")
    if resolved == workspace.root:
        raise PatchError("Patch cannot operate on the workspace root")
    # Check the lexical chain as well as resolve(): even in-bound links are
    # conservatively rejected by this writer, including dangling links.
    for part in (raw, *raw.parents):
        if part.is_symlink() or part.is_junction():
            raise PatchError("Symlink / junction paths are unsupported")
        if os.name == "nt" and os.path.lexists(part):
            if part.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise PatchError("Windows reparse point paths are unsupported")
        if part == workspace.root:
            break
    if workspace.root.resolve() != workspace.root:
        raise PatchError("Workspace changed since startup")
    return resolved


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    identity: tuple[int, int]
    digest: str | None
    mode: int


def _snapshot_file(path: Path) -> tuple[Snapshot, bytes]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise PatchError("Source must be a regular file")
    data = path.read_bytes()
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise PatchError("Source changed while being read")
    return Snapshot((before.st_dev, before.st_ino), _sha(data), stat.S_IMODE(before.st_mode)), data


def _snapshot_tree(workspace: WorkspaceManager, root: Path) -> dict[str, Snapshot]:
    result: dict[str, Snapshot] = {}
    pending = [root]
    while pending:
        path = pending.pop()
        _resolve(workspace, path)
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISDIR(info.st_mode):
            result[relative] = Snapshot((info.st_dev, info.st_ino), None, stat.S_IMODE(info.st_mode))
            pending.extend(path.iterdir())
        else:
            result[relative] = _snapshot_file(path)[0]
    return result


def _update_bytes(data: bytes, hunks: tuple[Hunk, ...]) -> bytes:
    bom, encoding = b"", "utf-8"
    for marker, codec in ((codecs.BOM_UTF8, "utf-8"),
                          (codecs.BOM_UTF16_LE, "utf-16-le"),
                          (codecs.BOM_UTF16_BE, "utf-16-be")):
        if data.startswith(marker):
            bom, encoding = marker, codec
            break
    try:
        text = data[len(bom):].decode(encoding, errors="strict")
    except UnicodeError as error:
        raise PatchError("Update supports UTF-8 and BOM-marked UTF-16 text only") from error
    if "\x00" in text:
        raise PatchError("Cannot update a binary file")
    without_crlf = text.replace("\r\n", "")
    styles = [value for value, found in (("\r\n", "\r\n" in text),
              ("\n", "\n" in without_crlf), ("\r", "\r" in without_crlf)) if found]
    if len(styles) > 1:
        raise PatchError("Mixed newline styles are unsupported")
    newline = styles[0] if styles else "\n"
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    terminated = normalized.endswith("\n")
    original = normalized.split("\n") if normalized else []
    if terminated:
        original.pop()
    output: list[str] = []
    cursor = 0
    for hunk in hunks:
        start = cursor
        if hunk.anchor is not None:
            anchors = [i for i in range(cursor, len(original)) if original[i] == hunk.anchor]
            if len(anchors) != 1:
                raise PatchError("Update anchor mismatch or ambiguous anchor")
            start = anchors[0] + 1
        old = [line[1:] for line in hunk.lines if line[0] != "+"]
        new = [line[1:] for line in hunk.lines if line[0] != "-"]
        matches = [i for i in range(start, len(original) - len(old) + 1)
                   if original[i:i + len(old)] == old
                   and (not hunk.at_end or i + len(old) == len(original))]
        if not matches:
            raise PatchError("Update context does not match exactly")
        if len(matches) != 1:
            raise PatchError("Update context is ambiguous")
        position = matches[0]
        output.extend(original[cursor:position])
        output.extend(new)
        cursor = position + len(old)
    output.extend(original[cursor:])
    final = newline.join(output) + (newline if terminated and output else "")
    return bom + final.encode(encoding, errors="strict")


@dataclass(frozen=True)
class Change:
    action: str
    source: Path
    destination: Path | None = None
    content: bytes | None = None
    snapshot: Snapshot | None = None
    tree: dict[str, Snapshot] | None = None

    def describe(self, workspace: WorkspaceManager) -> dict[str, str]:
        result = {"action": self.action, "path": self.source.relative_to(workspace.root).as_posix()}
        if self.destination is not None:
            result["destination"] = self.destination.relative_to(workspace.root).as_posix()
        return result


@dataclass(frozen=True)
class PatchPlan:
    workspace: WorkspaceManager
    changes: tuple[Change, ...]


def _require_absent(path: Path) -> None:
    if os.path.lexists(path):
        raise PatchError("Destination already exists")


def _check_parents(path: Path, root: Path) -> None:
    for parent in path.parents:
        if parent.exists() and not parent.is_dir():
            raise PatchError("Destination parent is not a directory")
        if parent == root:
            break


def prepare_patch(workspace: WorkspaceManager, patch: str) -> PatchPlan:
    """Complete validation and content generation without any filesystem writes."""
    changes: list[Change] = []
    used: list[Path] = []
    for operation in parse_patch(patch):
        label = operation.action
        try:
            source = _resolve(workspace, operation.path)
            label += " " + source.relative_to(workspace.root).as_posix()
            destination = _resolve(workspace, operation.destination) if operation.destination else None
            paths = [source] + ([destination] if destination else [])
            for index, path in enumerate(paths):
                for other in [*used, *paths[:index]]:
                    if path.is_relative_to(other) or other.is_relative_to(path):
                        raise PatchError("Patch operations have overlapping source / destination paths")
            used.extend(paths)
            if destination:
                _require_absent(destination)
                _check_parents(destination, workspace.root)
            if operation.action == "add":
                _require_absent(source)
                _check_parents(source, workspace.root)
                content = ("\n".join(operation.lines) + ("\n" if operation.lines else "")).encode("utf-8")
                changes.append(Change("add", source, content=content))
                continue
            if not source.exists():
                raise PatchError("Source does not exist")
            if operation.action == "move_directory":
                if not source.is_dir():
                    raise PatchError("Move Directory source must be a directory")
                changes.append(Change("move_directory", source, destination, tree=_snapshot_tree(workspace, source)))
                continue
            snapshot, data = _snapshot_file(source)
            if operation.action == "delete":
                changes.append(Change("delete", source, snapshot=snapshot))
                continue
            if operation.hunks:
                content = _update_bytes(data, operation.hunks)
                changes.append(Change("update", source, content=content, snapshot=snapshot))
                # The subsequent real rename protects the bytes produced by Update.
                snapshot = Snapshot(snapshot.identity, _sha(content), snapshot.mode)
            if destination:
                changes.append(Change("move", source, destination, snapshot=snapshot))
        except (ValueError, OSError, RuntimeError) as error:
            if isinstance(error, WorkspaceError):
                raise PatchError("Patch path is outside workspace") from error
            reason = (error.strerror or error.__class__.__name__) if isinstance(error, OSError) else str(error)
            raise PatchError(f"Cannot prepare {label}: {reason}") from error
    return PatchPlan(workspace, tuple(changes))


def _check_source(plan: PatchPlan, change: Change) -> None:
    if _resolve(plan.workspace, change.source) != change.source:
        raise PatchError("Source path changed")
    if change.tree is not None:
        if _snapshot_tree(plan.workspace, change.source) != change.tree:
            raise PatchError("Directory source SHA-256 / structure conflict")
    elif change.snapshot is not None:
        current, _ = _snapshot_file(change.source)
        # A temp replace changes the inode before the associated Move; its bytes
        # and mode remain protected, while other source operations check identity.
        same_identity = current.identity == change.snapshot.identity
        updated = change.action == "move" and any(
            c.action == "update" and c.source == change.source for c in plan.changes
        )
        if (
            current.digest != change.snapshot.digest
            or current.mode != change.snapshot.mode
            or (not same_identity and not updated)
        ):
            raise PatchError("Source SHA-256 / identity conflict")


def _rename_no_overwrite(source: Path, destination: Path) -> None:
    """Real atomic rename with destination exclusion, including POSIX races."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows rename fails if destination exists.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise OSError(errno.ENOTSUP, "No safe no-overwrite rename available on this platform")
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _make_parents(plan: PatchPlan, target: Path, created: list[str]) -> None:
    missing: list[Path] = []
    current = target.parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        _resolve(plan.workspace, directory)
        directory.mkdir()
        created.append(directory.relative_to(plan.workspace.root).as_posix())


def _finalize_add(temporary: Path, destination: Path) -> bool:
    """Return whether finalization consumed the temp path."""
    if os.name == "nt":
        _rename_no_overwrite(temporary, destination)
        return True
    # link() exclusively creates destination; replace() would clobber a file
    # appearing after the final existence check.
    os.link(temporary, destination)
    return False


def _write_file(
    plan: PatchPlan, change: Change, completed: list[dict[str, str]], temporary_files: list[str],
) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".chat2local-patch-", dir=change.source.parent, delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(change.content)
            handle.flush()
        if change.snapshot is not None:
            os.chmod(temporary, change.snapshot.mode)
            _check_source(plan, change)
            os.replace(temporary, change.source)
            temporary = None
        else:
            _resolve(plan.workspace, change.source)
            _require_absent(change.source)
            if _finalize_add(temporary, change.source):
                temporary = None
        completed.append(change.describe(plan.workspace))
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                temporary_files.append(temporary.relative_to(plan.workspace.root).as_posix())
                raise


def commit_patch(plan: PatchPlan) -> dict[str, Any]:
    """Commit sequentially, reporting actual progress on an I/O or race failure."""
    completed: list[dict[str, str]] = []
    created: list[str] = []
    temporary_files: list[str] = []
    for change in plan.changes:
        step = change.describe(plan.workspace)
        try:
            _resolve(plan.workspace, change.source)
            _check_source(plan, change)
            target = change.destination or change.source
            if change.destination or change.action == "add":
                _resolve(plan.workspace, target)
                _require_absent(target)
                _check_parents(target, plan.workspace.root)
                _make_parents(plan, target, created)
            if change.action in ("update", "add"):
                _write_file(plan, change, completed, temporary_files)
            elif change.action == "delete":
                _check_source(plan, change)
                change.source.unlink()
            else:
                _check_source(plan, change)
                _resolve(plan.workspace, target)
                _require_absent(target)
                _rename_no_overwrite(change.source, target)
            if change.action not in ("update", "add"):
                completed.append(step)
        except (ValueError, OSError, RuntimeError) as error:
            message = (error.strerror or error.__class__.__name__) if isinstance(error, OSError) else str(error)
            # Workspace errors may contain absolute input paths: return only a
            # fixed boundary message, and OSError's strerror (never filename).
            if isinstance(error, WorkspaceError):
                message = "Patch path is outside workspace"
            if temporary_files or (completed and completed[-1] == step):
                step = {**step, "phase": "temporary_file_cleanup"}
            return {"success": False, "partial": bool(completed or created or temporary_files),
                    "operations": completed,
                    "created_directories": created, "temporary_files": temporary_files,
                    "failed_operation": step, "error": message}
    return {"success": True, "partial": False, "operations": completed, "created_directories": created,
            "temporary_files": [], "failed_operation": None, "error": None}


async def apply_patch(workspace: WorkspaceManager, patch: str) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        return commit_patch(prepare_patch(workspace, patch))

    return await asyncio.to_thread(execute)
