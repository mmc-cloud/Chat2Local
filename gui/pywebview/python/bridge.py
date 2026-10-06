"""Transport and native dialogs only; Core management lives in the adapter."""

import asyncio
import logging
from importlib.metadata import PackageNotFoundError, version

from desktop_logging import log_exception_safe
from preferences import PreferencesStore
from pydantic import ValidationError

from chat2local.management.adapter import CoreAdapter
from chat2local.management.runtime import ManagementError
from chat2local.runtime.config import ConfigError
from chat2local.runtime.workspace import WorkspaceError

logger = logging.getLogger("chat2local.desktop.bridge")


class GuiBridge:
    def __init__(self, adapter: CoreAdapter | None = None, preferences=None) -> None:
        self._adapter = adapter or CoreAdapter()
        self._preferences = preferences or PreferencesStore()
        self._desktop = None
        self._window = None

    def _call(self, operation, *args):
        try:
            result = operation(*args)
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)
            return {"ok": True, "result": result}
        except ValidationError:
            return {"ok": False, "error": {"message": "Invalid GUI request"}}
        except (ManagementError, ConfigError, WorkspaceError) as error:
            return {"ok": False, "error": {"message": str(error)}}
        except Exception as error:  # noqa: BLE001 -- Desktop/native boundary, safe diagnostics.
            log_exception_safe(logger, "Internal GUI/Core error", error)
            return {
                "ok": False,
                "error": {"message": "Internal GUI/Core error; see logs"},
            }

    def ping(self):
        try:
            core_version = version("chat2local")
        except PackageNotFoundError:
            core_version = "unknown (package not installed)"
        return {
            "ok": True,
            "app": "Chat2Local",
            "version": core_version,
            "backend": "pywebview",
        }

    def runtime_status(self):
        result = self._observe(self._call(self._adapter.runtime_status))
        if result["ok"] and self._desktop:
            result["result"]["desktop_error"] = self._desktop.error
        return result

    def start_core(self, mode: str, workspace: str):
        return self._observe(
            self._call(self._adapter.runtime.action, "start", mode, workspace)
        )

    def stop_core(self):
        return self._call(self._adapter.runtime.action, "stop")

    def restart_core(self):
        return self._observe(self._call(self._adapter.runtime.action, "restart"))

    def _observe(self, result):
        if result["ok"]:
            try:
                self._preferences.remember_running(result["result"])
            except Exception as error:  # noqa: BLE001 -- Desktop/native boundary, safe diagnostics.
                log_exception_safe(
                    logger, "Could not save confirmed Core startup context", error
                )
                if self._desktop:
                    self._desktop.report_error(
                        "Could not save Core startup context; see desktop log"
                    )
        return result

    def read_desktop_preferences(self):
        return self._call(self._desktop_preferences)

    def _desktop_preferences(self):
        if self._desktop is None:
            raise ManagementError("Desktop shell unavailable")
        return self._desktop.read_preferences()

    def save_desktop_preferences(self, changes: dict):
        if self._desktop is None:
            return self._call(self._desktop_preferences)
        return self._call(self._desktop.save_preferences, changes)

    def take_desktop_navigation(self):
        return self._call(
            lambda: self._desktop.take_navigation() if self._desktop else None
        )

    def choose_workspace(self):
        return self._call(self._choose_folder)

    def _choose_folder(self):
        if self._window is None:
            raise ManagementError("Desktop window unavailable")
        import webview

        selected = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return selected[0] if selected else None

    def read_config(self):
        return self._call(self._adapter.read_config)

    def validate_config(self, candidate: dict):
        return self._call(self._adapter.validate_config, candidate)

    def save_config(self, candidate: dict):
        return self._call(self._adapter.save_config, candidate)

    def read_logs(self, cursor: dict | None = None):
        return self._call(self._adapter.logs.read, cursor)

    def list_devices(self):
        return self._call(self._adapter.runtime.devices)
