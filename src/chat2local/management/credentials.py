"""Explicit GUI credential actions. No automatic generation and no secret snapshots."""
from __future__ import annotations

import hmac
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading

from chat2local.management.runtime import ManagementError
from chat2local.runtime import config

_LOCK = threading.RLock()
_PURPOSES = ("hub", "agent", "browser")


def _private_file(path: Path) -> None:
    os.chmod(path, 0o600)
    if os.name != "nt":
        return
    # Do not inherit a potentially broadened parent ACL. OW applies to the
    # creator/owner; SYSTEM retains access. Apply before any secret is written.
    import ctypes
    from ctypes import wintypes
    security = ctypes.c_void_p()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW("D:P(A;;FA;;;OW)(A;;FA;;;SY)", 1, ctypes.byref(security), None):
        raise OSError("Could not secure credential file")
    try:
        if not advapi.SetFileSecurityW(str(path), 0x80000004, security):
            raise OSError("Could not secure credential file")
    finally:
        kernel.LocalFree(security)


class CredentialStore:
    def path(self, purpose: str, path: str | None = None) -> Path:
        if purpose not in _PURPOSES or (path is not None and not isinstance(path, str)):
            raise ManagementError("Invalid credential purpose or path")
        try:
            target = Path(path).expanduser() if path else config.user_data_directory() / f"{purpose}.token"
            if not target.is_absolute() or ".." in target.parts:
                raise ValueError
            for item in (target, *target.parents):
                if item.is_symlink() or item.is_junction():
                    raise ValueError
            return target
        except (OSError, ValueError, RuntimeError):
            raise ManagementError("Credential path must be absolute and cannot use links") from None

    def _value(self, purpose: str, value: str) -> str:
        if not isinstance(value, str):
            raise ManagementError("Invalid token")
        value = value.strip()
        if (not value or not value.isprintable() or len(value.encode("utf-8")) > 4096
                or (purpose == "browser" and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value) is None)):
            raise ManagementError("Invalid token")
        return value

    def read(self, purpose: str, path: str | None = None) -> str:
        target = self.path(purpose, path)
        try:
            with target.open("rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise OSError
                raw = handle.read(4097)
            if len(raw) > 4096:
                raise ValueError
            return self._value(purpose, raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise ManagementError("Credential file is unavailable or invalid") from None

    def info(self, purpose: str, path: str | None = None) -> dict:
        target = self.path(purpose, path)
        try:
            self.read(purpose, str(target))
        except ManagementError:
            return {"path": str(target), "state": "invalid" if target.exists() else "missing"}
        return {"path": str(target), "state": "ready"}

    def _independent(self, purpose: str, target: Path, value: str) -> None:
        settings = config.load_config()
        peers = ("hub", "agent") if purpose == "browser" else ("browser",)
        for peer in peers:
            peer_path = self.path(peer, getattr(settings, peer).token_file)
            if target == peer_path:
                raise ManagementError("Browser token must be independent of Hub and Agent tokens")
            try:
                other = self.read(peer, str(peer_path))
            except ManagementError:
                continue
            if hmac.compare_digest(value.encode(), other.encode()):
                raise ManagementError("Browser token must be independent of Hub and Agent tokens")
        shared = os.environ.get("CHAT2LOCAL_HUB_TOKEN")
        if purpose == "browser" and shared and hmac.compare_digest(value.encode(), shared.encode()):
            raise ManagementError("Browser token must be independent of Hub and Agent tokens")

    def _writable(self, target: Path) -> None:
        root = self.path("browser", str(config.user_data_directory()))
        if not target.is_relative_to(root) or target == root:
            raise ManagementError("New or regenerated credentials must be inside the private user data directory")
        if any((item / ".git").exists() for item in target.parents):
            raise ManagementError("Credentials cannot be written inside a Git repository")

    def write(self, purpose: str, path: str | None = None, *, value: str | None = None,
              regenerate: bool = False, confirmed: bool = False) -> dict:
        if type(regenerate) is not bool or type(confirmed) is not bool or (regenerate and value is not None):
            raise ManagementError("Invalid credential action")
        if regenerate and (purpose != "browser" or confirmed is not True):
            raise ManagementError("Browser token regeneration requires confirmation")
        if purpose == "agent" and value is None:
            raise ManagementError("Agent requires an existing Hub token; generation is not supported")
        target = self.path(purpose, path)
        with _LOCK:
            if target.exists():
                existing = self.read(purpose, str(target))
                self._independent(purpose, target, existing)
                if not regenerate:
                    if value is not None and not hmac.compare_digest(existing.encode(), self._value(purpose, value).encode()):
                        raise ManagementError("Credential file already exists with a different token; choose another path")
                    return {**self.info(purpose, str(target)), "reused": True}
            self._writable(target)
            token = self._value(purpose, value) if value is not None else secrets.token_urlsafe(32)
            self._independent(purpose, target, token)
            temporary = None
            try:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.path(purpose, str(target))  # Recheck newly created parents.
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".credential-", delete=False) as handle:
                    temporary = Path(handle.name)
                    _private_file(temporary)
                    handle.write(token.encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                if regenerate:
                    self.read(purpose, str(target))  # Reject links / invalid replacement targets.
                    os.replace(temporary, target)
                else:
                    try:
                        # Publish complete bytes exclusively; concurrent creators cannot overwrite.
                        os.link(temporary, target)
                    except FileExistsError:
                        raise ManagementError("Credential file was created concurrently; retry to reuse it") from None
                temporary.unlink(missing_ok=True)
                return {**self.info(purpose, str(target)), "reused": False}
            except OSError:
                raise ManagementError("Could not save credential file; existing credentials were preserved") from None
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass  # Do not replace the safe primary failure with a raw OS error.
