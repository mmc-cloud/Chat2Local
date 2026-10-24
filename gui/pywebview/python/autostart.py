"""Windows current-user login startup, isolated from Core and other Run entries."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from desktop_logging import log_exception_safe
from chat2local.runtime.windows_startup import RUN_KEY, WindowsRunEntry

VALUE_NAME = "Chat2Local"
logger = logging.getLogger("chat2local.desktop.autostart")


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def desktop_executable() -> str:
    if _frozen() or sys.platform != "win32":
        return sys.executable
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    try:
        if pythonw.is_file():
            return str(pythonw)
    except OSError as error:
        log_exception_safe(
            logger, "Could not inspect pythonw.exe; using current interpreter", error
        )
    logger.warning(
        "pythonw.exe unavailable; login startup fallback may display a console"
    )
    return sys.executable


def startup_command() -> str:
    """Keep frozen detection in one place; list2cmdline uses Windows quoting."""
    args = [desktop_executable()]
    if not _frozen():
        args.append(str(Path(__file__).resolve().with_name("app.py")))
    return subprocess.list2cmdline([*args, "--startup"])


class WindowsAutostart(WindowsRunEntry):
    def __init__(self) -> None:
        super().__init__(VALUE_NAME)

    def set_enabled(self, enabled: bool) -> None:
        registry = self._registry()
        desired = (startup_command(), registry.REG_SZ) if enabled else None
        if self.read() != desired:
            self.restore(desired)

    def reconcile(self, enabled: bool) -> None:
        """Match the persisted intent on primary startup; no-op off Windows."""
        if self.supported:
            self.set_enabled(enabled)
