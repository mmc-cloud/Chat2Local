"""Independent desktop preferences; never part of Core's config schema."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from desktop_logging import log_exception_safe

from chat2local.runtime import config

logger = logging.getLogger("chat2local.desktop.preferences")
MODES = {"standalone", "hub", "agent"}
EDITABLE = {
    "language",
    "launch_at_login",
    "silent_login_start",
    "close_behavior",
    "auto_start_core",
}


@dataclass(frozen=True)
class DesktopPreferences:
    schema_version: int = 1
    language: str = "zh-CN"
    launch_at_login: bool = False
    silent_login_start: bool = False
    close_behavior: str = "tray"
    auto_start_core: bool = False
    startup_mode: str | None = None
    startup_workspace: str | None = None

    @classmethod
    def parse(cls, raw: dict) -> DesktopPreferences:
        if not isinstance(raw, dict) or set(raw) - set(asdict(cls())):
            raise ValueError("Invalid desktop preferences fields")
        result = cls(**raw)
        if type(result.schema_version) is not int or result.schema_version != 1:
            raise ValueError("Unsupported desktop preferences schema")
        if result.language not in ("zh-CN", "en"):
            raise ValueError("Invalid desktop preference: language")
        for name in ("launch_at_login", "silent_login_start", "auto_start_core"):
            if type(getattr(result, name)) is not bool:
                raise ValueError(f"Invalid desktop preference: {name}")
        if result.close_behavior not in ("tray", "exit"):
            raise ValueError("Invalid desktop preference: close_behavior")
        if result.startup_mode is not None and (
            not isinstance(result.startup_mode, str) or result.startup_mode not in MODES
        ):
            raise ValueError("Invalid desktop preference: startup_mode")
        workspace = result.startup_workspace
        if workspace is not None and (
            not isinstance(workspace, str)
            or not workspace.strip()
            or "\0" in workspace
            or not Path(workspace).is_absolute()
        ):
            raise ValueError("Invalid desktop preference: startup_workspace")
        return result


class PreferencesStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.user_data_directory() / "gui" / "preferences.json"
        self.lock = threading.RLock()
        self.load_state = "missing"
        self.current = self._load()

    def _load(self) -> DesktopPreferences:
        try:
            preferences = DesktopPreferences.parse(
                json.loads(self.path.read_text(encoding="utf-8"))
            )
            self.load_state = "valid"
            return preferences
        except FileNotFoundError:
            self.load_state = "missing"
            return DesktopPreferences()
        except (OSError, ValueError, TypeError) as error:
            self.load_state = "invalid"
            log_exception_safe(
                logger, "Desktop preferences invalid/unreadable; using defaults", error
            )
            return DesktopPreferences()

    def save(self, preferences: DesktopPreferences) -> None:
        preferences = DesktopPreferences.parse(asdict(preferences))
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="\n",
                    dir=self.path.parent,
                    prefix=".preferences-",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    json.dump(asdict(preferences), handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                self.current = preferences
                self.load_state = "valid"
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def candidate(self, changes: dict) -> DesktopPreferences:
        if not isinstance(changes, dict) or set(changes) - EDITABLE:
            raise ValueError("Invalid desktop preferences fields")
        return DesktopPreferences.parse(asdict(replace(self.current, **changes)))

    def remember_running(self, snapshot: dict) -> None:
        core = snapshot.get("core")
        if (
            snapshot.get("lifecycle") != "Running"
            or not core
            or core.get("state") == "starting"
        ):
            return
        with self.lock:
            candidate = replace(
                self.current,
                startup_mode=core["mode"],
                startup_workspace=core["workspace"],
            )
            if candidate != self.current:
                self.save(candidate)
