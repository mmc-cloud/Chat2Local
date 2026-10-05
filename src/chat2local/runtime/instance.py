"""OS-owned Core lock and atomic, non-sensitive discovery descriptor."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import errno
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Literal
from uuid import uuid4


class RuntimeManagementError(RuntimeError):
    """Safe startup diagnostic for local runtime management."""


class AlreadyRunningError(RuntimeManagementError):
    def __init__(self) -> None:
        super().__init__("Chat2Local is already running")


class InstanceLock:
    """Keep an open, exclusively locked handle; file presence is irrelevant."""

    def __init__(self, directory: Path, *, platform: str | None = None) -> None:
        self.path = directory / "runtime.lock"
        self.platform = sys.platform if platform is None else platform
        self._handle = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise AlreadyRunningError()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            # msvcrt locks a byte range. Leave a permanent byte, never unlink the
            # lock file (doing so would let another process lock a different inode).
            if self.path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            self._lock(handle, unlock=False)
        except OSError as error:
            handle.close()
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise AlreadyRunningError() from None
            raise
        except BaseException:
            handle.close()
            raise
        self._handle = handle

    def _lock(self, handle, *, unlock: bool) -> None:
        if self.platform == "win32":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                self._lock(handle, unlock=True)
            finally:
                handle.close()


@dataclass(frozen=True)
class RuntimeDescriptor:
    instance_id: str
    pid: int
    mode: Literal["standalone", "hub", "agent"]
    device_id: str
    workspace: str
    started_at: str
    version: str
    control: dict[str, str | int]
    schema_version: int = 1

    @classmethod
    def new(cls, mode, device_id: str, workspace: Path, port: int) -> "RuntimeDescriptor":
        return cls(
            instance_id=str(uuid4()), pid=os.getpid(), mode=mode,
            device_id=device_id, workspace=str(workspace),
            started_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            version=version("chat2local"),
            control={"transport": "tcp", "host": "127.0.0.1", "port": port},
        )

    def publish(self, directory: Path) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix=".runtime-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(asdict(self), handle, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, directory / "runtime.json")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def remove(self, directory: Path) -> None:
        path = directory / "runtime.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            return
        if isinstance(data, dict) and data.get("instance_id") == self.instance_id:
            path.unlink(missing_ok=True)
