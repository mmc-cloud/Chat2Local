"""Session-local Windows desktop ownership and one-purpose activation IPC."""

from __future__ import annotations

import ctypes
import hmac
import json
import logging
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

from desktop_logging import log_exception_safe

from chat2local.runtime import config

logger = logging.getLogger("chat2local.desktop.instance")
MUTEX_NAME = r"Local\Chat2Local.Desktop"
MAX_DESCRIPTOR = 1024
MAX_REQUEST = 512


def _kernel32():
    # Deferred Windows import: other platforms can import the desktop shell.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateMutexW": (
            [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR],
            wintypes.HANDLE,
        ),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        "ReleaseMutex": ([wintypes.HANDLE], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "ProcessIdToSessionId": (
            [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        ),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    return kernel


class WindowsOwnership:
    def __init__(self, *, name=MUTEX_NAME):
        self.name = name
        self._kernel = _kernel32()
        self._handle = None
        self._owned = False
        session = wintypes.DWORD()
        if not self._kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            raise OSError("Could not determine Windows session")
        self.session_id = session.value

    def acquire(self) -> bool:
        self._handle = self._kernel.CreateMutexW(None, False, self.name)
        if not self._handle:
            raise OSError("Could not open desktop ownership mutex")
        result = self._kernel.WaitForSingleObject(self._handle, 0)
        if result in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED: now owned.
            self._owned = True
            return True
        self.close()
        if result == 0x102:  # WAIT_TIMEOUT: another thread/process owns it.
            return False
        raise OSError("Could not check desktop ownership mutex")

    def close(self):
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if self._owned and not self._kernel.ReleaseMutex(handle):
                raise OSError("Could not release desktop ownership mutex")
        finally:
            self._owned = False
            if not self._kernel.CloseHandle(handle):
                raise OSError("Could not close desktop ownership handle")


def _receive(connection, limit, timeout):
    deadline = time.monotonic() + timeout
    raw = bytearray()
    while len(raw) < limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        connection.settimeout(remaining)
        chunk = connection.recv(limit - len(raw))
        if not chunk:
            break
        raw.extend(chunk)
        if b"\n" in chunk:
            break
    return bytes(raw)


class SingleInstance:
    def __init__(
        self, directory=None, *, ownership=None, platform=None, retry_timeout=3
    ):
        self.supported = (sys.platform if platform is None else platform) == "win32"
        self.directory = (
            directory if directory is not None else config.user_data_directory() / "gui"
        )
        self.ownership = ownership
        self.retry_timeout = retry_timeout
        self.primary = False
        self.path = None
        self._nonce = secrets.token_hex(32)
        self._listener = None
        self._thread = None
        self._stopping = threading.Event()
        self._published = False

    def claim(self, *, startup=False) -> bool:
        if not self.supported:
            self.primary = True
            return True
        self.ownership = self.ownership or WindowsOwnership()
        self.path = self.directory / f"instance-{self.ownership.session_id}.json"
        self.primary = self.ownership.acquire()
        if self.primary:
            return True
        if not startup and not self.activate_existing():
            # A known owner is never bypassed, including during startup races.
            logger.error("Desktop primary exists but activation was unavailable")
        return False

    def _read_descriptor(self):
        with self.path.open("rb") as handle:
            raw = handle.read(MAX_DESCRIPTOR + 1)
        if len(raw) > MAX_DESCRIPTOR:
            raise ValueError("Invalid desktop activation descriptor")
        descriptor = json.loads(raw)
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "schema_version",
            "pid",
            "session_id",
            "port",
            "nonce",
        }:
            raise ValueError("Invalid desktop activation descriptor")
        for name in ("schema_version", "pid", "session_id", "port"):
            if type(descriptor[name]) is not int:
                raise ValueError("Invalid desktop activation descriptor")
        nonce = descriptor["nonce"]
        if (
            descriptor["schema_version"] != 1
            or descriptor["pid"] <= 0
            or descriptor["session_id"] != self.ownership.session_id
            or not 1 <= descriptor["port"] <= 65535
            or not isinstance(nonce, str)
            or len(nonce) != 64
            or any(character not in "0123456789abcdef" for character in nonce)
        ):
            raise ValueError("Invalid desktop activation descriptor")
        return descriptor

    def activate_existing(self) -> bool:
        deadline = time.monotonic() + self.retry_timeout
        while time.monotonic() < deadline:
            try:
                descriptor = self._read_descriptor()
                timeout = min(0.3, max(0.001, deadline - time.monotonic()))
                with socket.create_connection(
                    ("127.0.0.1", descriptor["port"]), timeout=timeout
                ) as connection:
                    connection.settimeout(timeout)
                    request = {"action": "activate", "nonce": descriptor["nonce"]}
                    connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
                    if _receive(connection, 32, timeout) == b"ok\n":
                        return True
            except (OSError, ValueError, TypeError):
                pass  # Expected readiness/stale-descriptor race; no payload logs.
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        return False

    def start(self, activate):
        if not self.supported:
            return
        if not self.primary:
            raise RuntimeError("Only desktop primary may listen for activation")
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._listener.bind(("127.0.0.1", 0))
            self._listener.listen(4)
            self._listener.settimeout(0.2)
            self._thread = threading.Thread(
                target=self._serve,
                args=(activate,),
                name="chat2local-activation",
                daemon=True,
            )
            self._thread.start()
            self._publish(
                {
                    "schema_version": 1,
                    "pid": os.getpid(),
                    "session_id": self.ownership.session_id,
                    "port": self._listener.getsockname()[1],
                    "nonce": self._nonce,
                }
            )
        except Exception:
            self.close()
            raise

    def _publish(self, descriptor):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=".instance-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(descriptor, handle)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self._published = True
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _serve(self, activate):
        while not self._stopping.is_set():
            try:
                connection, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError as error:
                if not self._stopping.is_set():
                    log_exception_safe(
                        logger, "Desktop activation listener failed", error
                    )
                return
            with connection:
                try:
                    raw = _receive(connection, MAX_REQUEST, 0.5)
                    request = json.loads(raw) if raw.endswith(b"\n") else None
                    valid = (
                        isinstance(request, dict)
                        and set(request) == {"action", "nonce"}
                        and request["action"] == "activate"
                        and isinstance(request["nonce"], str)
                        and hmac.compare_digest(request["nonce"], self._nonce)
                    )
                    if valid and not self._stopping.is_set():
                        # Only queues activation; an exiting desktop rejects it.
                        response = b"ok\n" if activate() is True else b"no\n"
                    else:
                        response = b"no\n"
                    connection.settimeout(0.2)
                    connection.sendall(response)
                except (OSError, ValueError, TypeError):
                    pass  # Bad/slow clients are rejected without logging their values.
                except Exception as error:  # noqa: BLE001 -- Desktop/native boundary, safe diagnostics.
                    log_exception_safe(
                        logger, "Desktop activation callback failed", error
                    )

    def close(self):
        self._stopping.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError as error:
                log_exception_safe(
                    logger, "Could not close desktop activation listener", error
                )
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
            if self._thread.is_alive():
                logger.error("Desktop activation listener shutdown timed out")
        if self._published:
            try:
                if self._read_descriptor()["nonce"] == self._nonce:
                    self.path.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError) as error:
                log_exception_safe(
                    logger, "Could not retire desktop activation descriptor", error
                )
            self._published = False
        if self.ownership is not None:
            try:
                self.ownership.close()
            except OSError as error:
                log_exception_safe(logger, "Could not close desktop ownership", error)
        self.primary = False
