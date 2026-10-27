"""Transport and native dialogs only; Core management lives in the adapter."""

import asyncio
import logging
import threading
from importlib.metadata import PackageNotFoundError, version

from desktop_logging import log_exception_safe
from clipboard import copy_text
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
        self._closing = False
        self._calls_lock = threading.Lock()
        self._calls = set()

    def _begin_shutdown(self):
        """Cancel GUI waits, without stopping or killing the independent Core."""
        with self._calls_lock:
            self._closing = True
            calls = tuple(self._calls)
        for loop, task in calls:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # The call completed and closed its loop concurrently.

    async def _await_call(self, coroutine):
        task = asyncio.create_task(coroutine)
        call = (asyncio.get_running_loop(), task)
        with self._calls_lock:
            self._calls.add(call)
            if self._closing:
                task.cancel()
        try:
            return await task
        finally:
            with self._calls_lock:
                self._calls.discard(call)

    def _call(self, operation, *args):
        try:
            with self._calls_lock:
                if self._closing:
                    return {"ok": False, "error": {"message": "Desktop is exiting"}}
            result = operation(*args)
            if asyncio.iscoroutine(result):
                result = asyncio.run(self._await_call(result))
            return {"ok": True, "result": result}
        except asyncio.CancelledError:
            return {"ok": False, "error": {"message": "Desktop is exiting"}}
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

    def list_local_handoffs(self):
        return self._call(self._adapter.list_local_handoffs)

    def get_local_handoff(self, handoff_id: str):
        return self._call(self._adapter.get_local_handoff, handoff_id)

    def save_local_handoff(self, handoff_id: str, title: str, summary: str,
                           content: str, expected_revision: int):
        return self._call(self._adapter.save_local_handoff, handoff_id, title, summary, content, expected_revision)

    def browser_status(self):
        return self._call(self._adapter.browser_status)

    def credential_info(self, purpose: str, path: str | None = None):
        return self._call(self._adapter.credential_info, purpose, path)

    def manage_token(self, purpose: str, path: str | None = None, value: str | None = None,
                     regenerate: bool = False, confirmed: bool = False):
        return self._call(self._adapter.manage_token, purpose, path, value, regenerate, confirmed)

    def copy_token(self, purpose: str, path: str | None = None):
        return self._call(self._copy_token, purpose, path)

    def _copy_token(self, purpose, path):
        copy_text(self._adapter.token_for_copy(purpose, path), self._window)
        return {"copied": True}
