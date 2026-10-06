"""Small Windows tray shell. Runtime work never runs on its message loop."""

from __future__ import annotations

import logging
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from desktop_logging import log_exception_safe
from paths import ASSETS

logger = logging.getLogger("chat2local.desktop.tray")
TRAY_TEXT = {
    "en": {
        "open": "Open Chat2Local",
        "start": "Start Core",
        "stop": "Stop Core",
        "restart": "Restart Core",
        "logs": "Open Logs",
        "exit": "Exit Chat2Local",
        "standalone": "Standalone",
        "hub": "Hub",
        "agent": "Agent",
        "Running": "Running",
        "Stopped": "Stopped",
        "Starting": "Starting",
        "Stopping": "Stopping",
        "Stale": "Stale",
        "Error": "Error",
    },
    "zh-CN": {
        "open": "打开 Chat2Local",
        "start": "启动 Core",
        "stop": "停止 Core",
        "restart": "重启 Core",
        "logs": "打开日志",
        "exit": "退出 Chat2Local",
        "standalone": "独立模式",
        "hub": "Hub",
        "agent": "Agent",
        "Running": "运行中",
        "Stopped": "已停止",
        "Starting": "启动中",
        "Stopping": "停止中",
        "Stale": "状态已失效",
        "Error": "错误",
    },
}


def create_icon(controller):
    import pystray
    from PIL import Image
    from pystray._util import win32

    # pystray 0.19.5's setup helper waits forever if native initialization fails
    # before _mark_ready. Run the tiny visibility setup on our owned loop instead,
    # so failure cannot strand that helper. No native mouse hooks are changed.
    class WindowsIcon(pystray.Icon):
        def _start_setup(self, setup):
            self._desktop_setup = setup
            self._setup_thread = threading.current_thread()
            self._thread = threading.current_thread()

        def _mark_ready(self):
            super()._mark_ready()
            self._desktop_setup(self)

        def _show(self):
            # pystray 0.19.5 ignores Shell_NotifyIcon's BOOL result. Check only
            # this call, so a failed native add cannot count as an available tray.
            notify = win32.Shell_NotifyIcon
            previous = notify.errcheck

            def check(result, function, arguments):
                if not result:
                    raise OSError("Windows could not display the tray icon")
                return result

            notify.errcheck = check
            try:
                super()._show()
            finally:
                if previous is None:
                    del notify.errcheck
                else:
                    notify.errcheck = previous

    with Image.open(ASSETS / "chat2local_64.png") as source:
        image = source.convert("RGBA")
    item = pystray.MenuItem
    menu = pystray.Menu(
        item(lambda _: controller.text("open"), lambda: controller.submit("open"), default=True),
        item(lambda _: controller.status_text, None, enabled=False),
        item(
            lambda _: controller.text("start"),
            lambda: controller.submit("start"),
            enabled=lambda _: controller.can_start,
        ),
        item(
            lambda _: controller.text("stop"),
            lambda: controller.submit("stop"),
            enabled=lambda _: controller.lifecycle == "Running",
        ),
        item(
            lambda _: controller.text("restart"),
            lambda: controller.submit("restart"),
            enabled=lambda _: controller.lifecycle == "Running",
        ),
        item(lambda _: controller.text("logs"), lambda: controller.submit("logs")),
        item(lambda _: controller.text("exit"), lambda: controller.submit("exit")),
    )
    return WindowsIcon("Chat2Local", image, "Chat2Local", menu)


class TrayController:
    def __init__(self, desktop, *, icon_factory=create_icon, platform=None) -> None:
        self.desktop = desktop
        self.icon_factory = icon_factory
        self.platform = sys.platform if platform is None else platform
        self.icon = None
        self.available = False
        self.snapshot = {"lifecycle": "Stopped", "core": None}
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._icon_stopping = threading.Event()
        self._commands = queue.Queue(maxsize=1)
        self._loop = None
        self._worker = None
        self._operations = None
        self._operation = None
        self._last_menu_state = None

    @property
    def lifecycle(self):
        return self.snapshot["lifecycle"]

    @property
    def language(self):
        return self.desktop.preferences.current.language

    def text(self, key):
        return TRAY_TEXT.get(self.language, TRAY_TEXT["zh-CN"]).get(key, key)

    @property
    def status_text(self):
        core = self.snapshot.get("core")
        mode = core["mode"] if core else self.desktop.preferences.current.startup_mode
        return f"Core: {self.text(mode) if mode else '—'} · {self.text(self.lifecycle)}"

    @property
    def can_start(self):
        preferences = self.desktop.preferences.current
        return self.lifecycle == "Stopped" and bool(
            preferences.startup_mode and preferences.startup_workspace
        )

    @property
    def menu_state(self):
        return (self.language, self.status_text, self.can_start, self.lifecycle)

    def start(self, timeout=5) -> bool:
        if self.platform != "win32":
            logger.info("Windows system tray is not supported on this platform")
            return False
        try:
            self.icon = self.icon_factory(self)
            self._loop = threading.Thread(
                target=self._run_icon, name="chat2local-tray", daemon=True
            )
            self._loop.start()
            if not self._ready.wait(timeout) or not self.available:
                raise RuntimeError("Tray did not become ready")
            self._last_menu_state = self.menu_state
            return True
        except Exception as error:  # noqa: BLE001 -- Desktop/native boundary, safe diagnostics.
            log_exception_safe(
                logger,
                "Tray initialization failed; keeping desktop window visible",
                error,
            )
            self._stop_icon()
            return False

    def _setup(self, icon):
        if not self._icon_stopping.is_set():
            icon.visible = True
            self.available = True
        self._ready.set()
        if self._icon_stopping.is_set():
            icon.stop()

    def _run_icon(self):
        try:
            self.icon.run(setup=self._setup)
        except Exception as error:  # noqa: BLE001 -- Desktop/native boundary, safe diagnostics.
            log_exception_safe(logger, "Tray message loop failed", error)
        finally:
            self.available = False
            self._ready.set()
            if not self._icon_stopping.is_set():
                self.desktop.tray_failed()

    def start_worker(self):
        if self._stopping.is_set():
            return
        self._operations = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="chat2local-core"
        )
        self._worker = threading.Thread(
            target=self._work, name="chat2local-desktop", daemon=True
        )
        self._worker.start()

    def submit(self, command):
        if self._stopping.is_set():
            return
        if command == "exit":
            try:
                self._commands.get_nowait()
            except queue.Empty:
                pass
        try:
            self._commands.put_nowait(command)
        except queue.Full:
            # A pending action is enough; never queue repeated lifecycle clicks.
            pass

    def _work(self):
        try:
            self._operation = self._operations.submit(
                self._operate, self.desktop.auto_start_core
            )
            while not self._stopping.is_set():
                self.desktop.activate_pending()
                self.refresh()
                try:
                    command = self._commands.get(timeout=1)
                except queue.Empty:
                    continue
                if not self._stopping.is_set():
                    if command in ("start", "stop", "restart"):
                        if self._operation is None or self._operation.done():
                            self._operation = self._operations.submit(
                                self._operate, self.perform, command
                            )
                    else:
                        self.perform(command)
        except Exception as error:  # noqa: BLE001 -- Desktop/native boundary, safe diagnostics.
            log_exception_safe(logger, "Desktop worker failed", error)
            self.desktop.report_error("Desktop operation failed; see desktop log")

    def _operate(self, operation, *args):
        if self._stopping.is_set() or self.desktop.exiting:
            return
        try:
            operation(*args)
        except Exception as error:  # noqa: BLE001 -- Desktop/native boundary, safe diagnostics.
            log_exception_safe(logger, "Desktop Core operation failed", error)
            self.desktop.report_error("Desktop operation failed; see desktop log")

    def refresh(self):
        result = self.desktop.bridge.runtime_status()
        if result["ok"]:
            self.snapshot = result["result"]
        else:
            self.snapshot = {"lifecycle": "Error", "core": None}
        menu_state = self.menu_state
        if self.available and menu_state != self._last_menu_state:
            try:
                self.icon.update_menu()
                self._last_menu_state = menu_state
            except Exception as error:  # noqa: BLE001 -- Desktop/native boundary, safe diagnostics.
                log_exception_safe(logger, "Could not update tray menu", error)
                self.available = False
                self.desktop.tray_failed()

    def perform(self, command):
        if command == "open":
            self.desktop.open_window()
        elif command == "logs":
            self.desktop.open_logs()
        elif command == "exit":
            self.desktop.exit()
        else:
            if command == "start":
                preferences = self.desktop.preferences.current
                if not self.can_start:
                    self.desktop.open_window()
                    return
                result = self.desktop.bridge.start_core(
                    preferences.startup_mode, preferences.startup_workspace
                )
            elif command == "stop":
                result = self.desktop.bridge.stop_core()
            elif command == "restart":
                result = self.desktop.bridge.restart_core()
            else:
                raise ValueError("Unknown tray action")
            if not result["ok"]:
                logger.error("Tray Core operation failed")
                self.desktop.report_error(result["error"]["message"])

    def shutdown(self):
        self._stopping.set()
        self._stop_icon()
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join()
        if self._operations is not None:
            self._operations.shutdown(wait=True, cancel_futures=True)

    def _stop_icon(self):
        self._icon_stopping.set()
        self.available = False
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception as error:  # noqa: BLE001 -- Desktop/native boundary, safe diagnostics.
                log_exception_safe(logger, "Could not stop tray icon", error)
        if self._loop is not None and self._loop is not threading.current_thread():
            self._loop.join(timeout=5)
            if self._loop.is_alive():
                logger.error("Tray message loop did not shut down in time")
