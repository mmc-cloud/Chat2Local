"""Independent Core login startup; never start/stop the current service."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile

from chat2local.management.runtime import ManagementError
from chat2local.runtime import config
from chat2local.runtime.config import AutostartConfig
from chat2local.runtime.windows_startup import WindowsRunEntry

CORE_RUN_VALUE = "Chat2LocalCore"
USER_UNIT = "chat2local-core.service"
LAUNCH_LABEL = "org.chat2local.core"


@dataclass(frozen=True)
class StartupCommand:
    args: tuple[str, ...]
    directory: Path
    unavailable_reason: str | None = None

    def checked(self) -> tuple[str, ...]:
        if self.unavailable_reason:
            raise ManagementError(self.unavailable_reason)
        if any("\0" in item or "\n" in item or "\r" in item for item in self.args):
            raise ManagementError("Startup arguments contain unsupported control characters")
        return self.args


def _plain_path(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ManagementError("Startup configuration path must be absolute")
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ManagementError("Startup configuration cannot use links")


def _atomic_write(path: Path, data: bytes, *, private_windows: bool = False) -> None:
    _plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _plain_path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".autostart-", delete=False) as handle:
            temporary = Path(handle.name)
            if private_windows:
                from chat2local.management.credentials import _private_file
                _private_file(temporary)
            else:
                os.chmod(temporary, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _run(args: list[str], *, missing_ok: bool = False) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ManagementError("Startup service manager is unavailable or timed out") from None
    if result.returncode and not missing_ok:
        # Never echo a service's ExecStart, environment, or manager stderr.
        raise ManagementError("Startup operation failed; check service permissions and service configuration")
    return result.stdout


class CoreAutostart:
    def __init__(self, command: StartupCommand, settings: AutostartConfig | None = None) -> None:
        self.command = command
        self.settings = settings or AutostartConfig()
        self.windows = WindowsRunEntry(CORE_RUN_VALUE)

    def status(self) -> dict:
        try:
            if sys.platform == "win32":
                return self._windows_status()
            if sys.platform.startswith("linux"):
                return self._linux_status()
            if sys.platform == "darwin":
                return self._mac_status()
            return {"supported": False, "enabled": None, "backend": "unsupported", "state": "unavailable"}
        except OSError:
            raise ManagementError("Could not read startup configuration; check permissions") from None

    def set_enabled(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise ManagementError("Invalid startup setting")
        try:
            if sys.platform == "win32":
                registry = self.windows._registry()
                bootstrap = None
                previous = None
                if enabled:
                    command_line, bootstrap, source = self._windows_launch()
                    if bootstrap is not None:
                        _plain_path(bootstrap)
                        previous = bootstrap.read_bytes() if bootstrap.exists() else None
                        _atomic_write(bootstrap, source.encode("utf-8"), private_windows=True)
                    desired = (command_line, registry.REG_SZ)
                else:
                    desired = None
                try:
                    if self.windows.read() != desired:
                        self.windows.restore(desired)
                except OSError:
                    if bootstrap is not None:
                        if previous is None:
                            bootstrap.unlink(missing_ok=True)
                        else:
                            _atomic_write(bootstrap, previous, private_windows=True)
                    raise
            elif sys.platform.startswith("linux"):
                self._linux_set(enabled)
            elif sys.platform == "darwin":
                self._mac_set(enabled)
            else:
                raise ManagementError("Login startup is not supported on this platform")
            result = self.status()
            if result["enabled"] is not enabled:
                raise ManagementError("Startup change could not be confirmed; refresh service state")
            return result
        except OSError:
            raise ManagementError("Could not change startup configuration; check permissions") from None

    def _windows_launch(self) -> tuple[str, Path | None, str | None]:
        """Calculate the current command for both validation and repair, without executing it."""
        args = list(self.command.checked())
        pythonw = Path(args[0]).with_name("pythonw.exe")
        if pythonw.is_file():
            args[0] = str(pythonw)
        if not Path(args[0]).is_file() or not self.command.directory.is_dir():
            raise ManagementError("Startup Python environment or working directory is missing")
        command_line = subprocess.list2cmdline(args)
        if len(command_line.encode("utf-16-le")) // 2 <= 260:
            return command_line, None, None
        if args[1:3] != ["-m", "chat2local.main"]:
            raise ManagementError("Startup command is too long for the Windows Run entry")
        bootstrap = config.user_data_directory() / "windows-core-startup.py"
        command_line = subprocess.list2cmdline([args[0], str(bootstrap)])
        if len(command_line.encode("utf-16-le")) // 2 > 260:
            raise ManagementError("Python environment path is too long for Windows login startup; use a shorter environment path")
        source = ("# Generated by Chat2Local for independent Core login startup.\n"
                  "import sys\nfrom chat2local.main import main\n"
                  f"sys.argv = {['chat2local', *args[3:]]!r}\nmain()\n")
        return command_line, bootstrap, source

    def _windows_status(self) -> dict:
        value = self.windows.read()
        result = {"supported": True, "enabled": False, "backend": "windows-run",
                  "scope": "user", "service": CORE_RUN_VALUE, "state": "disabled"}
        if value is None:
            return result
        valid = False
        try:
            command, bootstrap, source = self._windows_launch()
            valid = value == (command, self.windows._registry().REG_SZ)
            if valid and bootstrap is not None:
                _plain_path(bootstrap)
                valid = bootstrap.is_file() and bootstrap.read_bytes() == source.encode("utf-8")
        except ManagementError:
            pass  # An obsolete/invalid context is repairable, not a healthy enabled entry.
        result.update(enabled=True if valid else None, state="enabled" if valid else "stale")
        return result

    def _systemctl(self, scope: str, *args: str, missing_ok: bool = False) -> str:
        return _run(["systemctl", *( ["--user"] if scope == "user" else []),
                     "--no-ask-password", *args], missing_ok=missing_ok)

    def _show(self, scope: str, unit: str) -> dict[str, str]:
        raw = self._systemctl(scope, "show", unit,
                             "--property=LoadState,UnitFileState,FragmentPath,ExecStart,ControlGroup,MainPID",
                             missing_ok=True)
        info = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        if "LoadState" not in info:
            raise ManagementError("Startup service manager is unavailable; check service permissions")
        return info

    def _linux_target(self) -> tuple[str, str, dict[str, str]]:
        try:
            groups = [line.split(":", 2)[2] for line in Path("/proc/self/cgroup").read_text().splitlines()]
        except (OSError, IndexError):
            groups = []
        if self.settings.service:
            scope, unit = self.settings.scope or "user", self.settings.service
        else:
            candidates = [(group, group.rstrip("/").split("/")[-1]) for group in groups
                          if group.endswith(".service") and not re.search(r"/user(?:-runtime-dir)?@\d+\.service$", group)]
            if candidates:
                group, unit = candidates[0]
                scope = "user" if re.search(r"/user@\d+\.service/", group) else "system"
            else:
                scope, unit = self.settings.scope or "user", USER_UNIT
        # Unit names from cgroups and config are arguments, never shell patterns/options.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@\\:-]*\.service", unit):
            raise ManagementError("Invalid systemd service name")
        info = self._show(scope, unit)
        if info.get("LoadState") == "masked":
            return scope, unit, info  # Read-only state; enable/disable cannot unmask a unit.
        if info.get("LoadState") == "not-found":
            if self.settings.service or scope == "system" or unit != USER_UNIT:
                raise ManagementError("Configured systemd service was not found")
        elif (info.get("LoadState") != "loaded" or not (
            "chat2local" in info.get("ExecStart", "").lower()
            or info.get("ControlGroup") in groups
            or info.get("MainPID") == str(os.getpid())
        )):
            raise ManagementError("Select an existing Chat2Local systemd service using autostart.service and autostart.scope")
        return scope, unit, info

    def _linux_status(self) -> dict:
        scope, unit, info = self._linux_target()
        state = (info["LoadState"] if info.get("LoadState") in ("not-found", "masked")
                 else info.get("UnitFileState", "unknown"))
        enabled = {"enabled": True, "disabled": False, "not-found": False}.get(state)
        return {"supported": True, "enabled": enabled, "backend": "systemd", "scope": scope,
                "service": unit, "path": info.get("FragmentPath") or str(self._unit_path()), "state": state}

    def _unit_path(self) -> Path:
        return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd/user" / USER_UNIT

    def _linux_set(self, enabled: bool) -> None:
        scope, unit, info = self._linux_target()
        if info.get("LoadState") == "not-found":
            if not enabled:
                return
            path = self._unit_path()
            # Never replace a unit the manager has not loaded yet.
            _plain_path(path)
            if path.exists():
                raise ManagementError("A startup unit already exists; reload the user systemd manager before continuing")
            args = self.command.checked()
            def quote(value: str, *, command_arg: bool = True) -> str:
                value = value.replace("%", "%%")
                return json.dumps(value.replace("$", "$$") if command_arg else value, ensure_ascii=False)
            content = ("[Unit]\nDescription=Chat2Local independent Core\n\n[Service]\nType=simple\n"
                       f"WorkingDirectory={quote(str(self.command.directory), command_arg=False)}\n"
                       f"ExecStart={' '.join(quote(arg) for arg in args)}\n\n[Install]\nWantedBy=default.target\n")
            _atomic_write(path, content.encode("utf-8"))
            self._systemctl("user", "daemon-reload")
        elif info.get("UnitFileState") not in ("enabled", "disabled"):
            raise ManagementError("This systemd unit cannot be managed with a persistent startup switch")
        # No --now: changing future startup must not interrupt this Core.
        self._systemctl(scope, "enable" if enabled else "disable", unit)

    def _plist_path(self) -> Path:
        return Path.home() / "Library/LaunchAgents" / f"{LAUNCH_LABEL}.plist"

    def _plist(self) -> dict | None:
        path = self._plist_path()
        _plain_path(path)
        try:
            data = plistlib.loads(path.read_bytes())
        except FileNotFoundError:
            return None
        except (ValueError, plistlib.InvalidFileException):
            raise ManagementError("LaunchAgent configuration is invalid") from None
        if (not isinstance(data, dict) or data.get("Label") != LAUNCH_LABEL or not isinstance(data.get("ProgramArguments"), list)
                or "chat2local.main" not in data["ProgramArguments"]):
            raise ManagementError("Existing LaunchAgent is not an independent Chat2Local Core")
        return data

    def _mac_status(self) -> dict:
        data = self._plist()
        disabled = _run(["launchctl", "print-disabled", f"gui/{os.getuid()}"])
        match = re.search(r'"' + re.escape(LAUNCH_LABEL) + r'"\s*=>\s*(true|false)', disabled)
        disabled_by_override = match[1] == "true" if match else bool(data and data.get("Disabled"))
        enabled = data is not None and data.get("RunAtLoad") is True and not disabled_by_override
        return {"supported": True, "enabled": bool(enabled), "backend": "launchd", "scope": "user",
                "service": LAUNCH_LABEL, "path": str(self._plist_path()), "state": "enabled" if enabled else "disabled"}

    def _mac_set(self, enabled: bool) -> None:
        previous = self._plist()
        path = self._plist_path()
        before = path.read_bytes() if previous is not None else None
        changed = False
        if enabled:
            data = {**(previous or {}), "Label": LAUNCH_LABEL, "ProgramArguments": list(self.command.checked()),
                    "WorkingDirectory": str(self.command.directory), "RunAtLoad": True}
            if "Disabled" in data:
                data["Disabled"] = False
            if data != previous:
                _atomic_write(path, plistlib.dumps(data))
                changed = True
        elif previous is None:
            return
        try:
            # enable/disable only update launchd's persistent override. Never bootout,
            # unload, bootstrap or kickstart a running Core from this switch.
            _run(["launchctl", "enable" if enabled else "disable", f"gui/{os.getuid()}/{LAUNCH_LABEL}"])
        except ManagementError:
            if changed:
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write(path, before)
            raise
