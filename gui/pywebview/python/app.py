"""Replaceable pywebview shell. Run from any working directory."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from contextlib import contextmanager
from dataclasses import asdict
from ipaddress import ip_address
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

from autostart import WindowsAutostart
from bridge import GuiBridge
from desktop_logging import DesktopSafeFormatter, bootstrap_logging, log_exception_safe
from preferences import PreferencesStore
from single_instance import SingleInstance
from tray import TrayController

from chat2local.management.runtime import ManagementError
from chat2local.runtime import config as core_config

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
logger = logging.getLogger("chat2local.desktop.app")


class DesktopShell:
    def __init__(self, *, startup=False, preferences=None, bridge=None, autostart=None):
        self.preferences = preferences or PreferencesStore()
        self.bridge = bridge or GuiBridge(preferences=self.preferences)
        self.bridge._desktop = self
        self.autostart = autostart or WindowsAutostart()
        self.startup = startup
        self.window = None
        self.tray = TrayController(self)
        self.exiting = False
        self.error = None
        self._navigation = None
        self._navigation_lock = threading.Lock()
        self._activation_requested = threading.Event()

    @property
    def hidden_start(self):
        return bool(
            self.startup
            and self.preferences.current.silent_login_start
            and self.tray.available
        )

    def read_preferences(self):
        return {
            "preferences": asdict(self.preferences.current),
            "launch_at_login_supported": self.autostart.supported,
            "tray_available": self.tray.available,
        }

    def save_preferences(self, changes):
        with self.preferences.lock:
            try:
                candidate = self.preferences.candidate(changes)
            except (ValueError, TypeError):
                raise ManagementError("Invalid desktop preferences fields") from None
            registry_change = "launch_at_login" in changes
            previous = None
            if registry_change:
                try:
                    previous = self.autostart.read()
                    self.autostart.set_enabled(candidate.launch_at_login)
                except ValueError as error:
                    raise ManagementError(str(error)) from None
                except OSError as error:
                    log_exception_safe(
                        logger, "Could not update Windows login startup", error
                    )
                    raise ManagementError(
                        "Could not update Windows login startup; see desktop log"
                    ) from None
            try:
                self.preferences.save(candidate)
            except OSError as error:
                if registry_change:
                    try:
                        self.autostart.restore(previous)
                    except OSError as rollback_error:
                        log_exception_safe(
                            logger,
                            "Could not roll back Windows login startup",
                            rollback_error,
                        )
                        self.report_error(
                            "Windows login startup rollback failed; see desktop log"
                        )
                log_exception_safe(logger, "Could not save desktop preferences", error)
                raise ManagementError(
                    "Could not save desktop preferences; see desktop log"
                ) from None
        return self.read_preferences()

    def reconcile_login_startup(self):
        if self.preferences.load_state == "invalid":
            self.report_error(
                "Desktop preferences invalid or unreadable; Windows login startup was not changed"
            )
            return
        try:
            self.autostart.reconcile(self.preferences.current.launch_at_login)
        except Exception as error:  # noqa: BLE001 -- Native registry boundary, safe diagnostics.
            log_exception_safe(
                logger, "Could not synchronize Windows login startup", error
            )
            self.report_error(
                "Could not synchronize Windows login startup; see desktop log"
            )

    def before_show(self):
        # Recheck in the native lifecycle in case the tray died after initialization.
        self.window.hidden = self.hidden_start

    def on_closing(self):
        if (
            not self.exiting
            and self.preferences.current.close_behavior == "tray"
            and self.tray.available
        ):
            try:
                self.window.hide()
                return False
            except Exception as error:  # noqa: BLE001 -- Desktop/native boundary, safe diagnostics.
                log_exception_safe(logger, "Could not hide window; exiting GUI", error)
        self.exiting = True
        return True

    def open_window(self):
        if self.window is not None and not self.exiting:
            self.window.show()
            self.window.restore()
            # pywebview 6.2.1 WinForms show() calls Activate(); no focus() API.
            self.window.show()

    def request_activation(self) -> bool:
        # IPC only signals; the existing desktop worker calls the native backend.
        if self.exiting:
            return False
        self._activation_requested.set()
        return True

    def activate_pending(self):
        if self._activation_requested.is_set():
            self._activation_requested.clear()
            self.open_window()

    def open_logs(self):
        with self._navigation_lock:
            self._navigation = "Logs"
        self.open_window()

    def take_navigation(self):
        with self._navigation_lock:
            page, self._navigation = self._navigation, None
            return page

    def exit(self):
        self.exiting = True
        if self.window is not None:
            self.window.destroy()

    def report_error(self, message):
        self.error = message
        logger.error("Desktop error reported to GUI")

    def tray_failed(self):
        self.report_error("Tray unavailable; closing the window will exit Chat2Local")
        if self.window is not None:
            self.open_window()

    def auto_start_core(self):
        preferences = self.preferences.current
        if not preferences.auto_start_core or self.exiting:
            return
        status = self.bridge.runtime_status()
        if not status["ok"]:
            self.report_error(status["error"]["message"])
            return
        lifecycle = status["result"]["lifecycle"]
        if lifecycle != "Stopped":
            state = (
                lifecycle
                if lifecycle in ("Running", "Starting", "Stopping", "Error")
                else "Unknown"
            )
            logger.info("Automatic Core start skipped: %s", state)
            return
        if not preferences.startup_mode or not preferences.startup_workspace:
            logger.warning("Automatic Core start skipped: no confirmed mode/workspace")
            return
        if self.exiting:
            return
        result = self.bridge.start_core(
            preferences.startup_mode, preferences.startup_workspace
        )
        if not result["ok"]:
            self.report_error(result["error"]["message"])

    def shutdown(self):
        self.exiting = True
        self.tray.shutdown()


@contextmanager
def desktop_logging():
    """Separate desktop diagnostics; never configure Core's log handlers."""
    directory = core_config.user_data_directory() / "gui"
    desktop_logger = logging.getLogger("chat2local.desktop")
    previous_level, previous_propagate = desktop_logger.level, desktop_logger.propagate
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            directory / "desktop.log",
            maxBytes=1_000_000,
            backupCount=1,
            encoding="utf-8",
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(
        DesktopSafeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    desktop_logger.addHandler(handler)
    desktop_logger.setLevel(logging.INFO)
    desktop_logger.propagate = False
    try:
        yield
    finally:
        desktop_logger.removeHandler(handler)
        desktop_logger.setLevel(previous_level)
        desktop_logger.propagate = previous_propagate
        handler.close()


def frontend_url(dev_url: str | None) -> str:
    if dev_url:
        message = "--dev-url must be a loopback http(s) URL without credentials, query or fragment"
        try:
            parsed = urlsplit(dev_url)
            hostname = parsed.hostname
            if parsed.port is not None and parsed.port == 0:
                raise ValueError
            loopback = hostname == "localhost"
            if hostname and not loopback:
                try:
                    loopback = ip_address(hostname).is_loopback
                except ValueError:
                    loopback = False
        except ValueError:
            raise ValueError(message) from None
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or not loopback
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(message)
        return dev_url
    index = FRONTEND / "dist" / "index.html"
    if not index.is_file():
        raise FileNotFoundError(
            "Frontend build missing. Run pnpm install and pnpm build "
            "in gui/pywebview/frontend first."
        )
    # An explicit file URI avoids pywebview's automatic server for local paths.
    return index.as_uri()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dev-url", help="Loopback Vite URL, usually http://127.0.0.1:5173"
    )
    parser.add_argument(
        "--startup", action="store_true", help="Launched at Windows sign-in"
    )
    args = parser.parse_args()
    with bootstrap_logging():
        instance = None
        try:
            try:
                instance = SingleInstance()
                if not instance.claim(startup=args.startup):
                    return
            except Exception as error:  # noqa: BLE001 -- Bootstrap must not expose native exception values.
                log_exception_safe(logger, "Desktop ownership/activation failed", error)
                raise SystemExit(1) from None
            with desktop_logging():
                desktop = None
                try:
                    desktop = DesktopShell(startup=args.startup)
                    desktop.reconcile_login_startup()
                    try:
                        url = frontend_url(args.dev_url)
                    except (ValueError, FileNotFoundError) as exc:
                        parser.exit(2, f"{exc}\n")
                    import webview

                    instance.start(desktop.request_activation)
                    desktop.tray.start()
                    desktop.window = desktop.bridge._window = webview.create_window(
                        "Chat2Local",
                        url=url,
                        js_api=desktop.bridge,
                        width=1100,
                        height=720,
                        min_size=(900, 600),
                        resizable=True,
                        hidden=desktop.hidden_start,
                    )
                    desktop.window.events.before_show += desktop.before_show
                    desktop.window.events.closing += desktop.on_closing
                    webview.start(
                        desktop.tray.start_worker,
                        gui="edgechromium" if sys.platform == "win32" else None,
                        http_server=False,
                        private_mode=False,
                        storage_path=str(
                            core_config.user_data_directory() / "gui" / "webview"
                        ),
                    )
                except Exception as error:
                    log_exception_safe(logger, "Desktop startup failed", error)
                    raise
                finally:
                    if desktop is not None:
                        desktop.shutdown()
        finally:
            # Close the file handler before releasing ownership to a new primary.
            if instance is not None:
                instance.close()


if __name__ == "__main__":
    main()
