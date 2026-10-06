"""Isolated Windows app composition smoke host; fake GUI, Registry and Core."""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace


def main():
    source, directory, mutex_name, role = sys.argv[1:]
    sys.path.insert(0, source)
    import app
    from preferences import PreferencesStore
    from single_instance import SingleInstance, WindowsOwnership

    directory = Path(directory)
    app.core_config.user_data_directory = lambda: directory
    trace = {
        "primary": None,
        "file_handlers": 0,
        "shells": 0,
        "reconciles": 0,
        "tray_starts": 0,
        "windows": 0,
        "auto_start_calls": 0,
        "activations": 0,
        "activation_succeeded": False,
        "file_closed_before_release": None,
        "listener_alive_after_close": False,
    }
    handlers = []
    original_handler = app.RotatingFileHandler

    def rotating_handler(*args, **kwargs):
        trace["file_handlers"] += 1
        handler = original_handler(*args, **kwargs)
        handlers.append(handler)
        return handler

    app.RotatingFileHandler = rotating_handler

    class TracedInstance(SingleInstance):
        def claim(self, **kwargs):
            trace["primary"] = super().claim(**kwargs)
            return trace["primary"]

        def activate_existing(self):
            trace["activation_succeeded"] = super().activate_existing()
            return trace["activation_succeeded"]

        def close(self):
            if self.primary:
                trace["file_closed_before_release"] = bool(handlers) and all(
                    handler.stream is None for handler in handlers
                )
            super().close()
            trace["listener_alive_after_close"] = (
                self._thread is not None and self._thread.is_alive()
            )

    app.SingleInstance = lambda: TracedInstance(
        directory / "gui", ownership=WindowsOwnership(name=mutex_name), retry_timeout=1
    )

    class FakeAutostart:
        supported = True

        def reconcile(self, enabled):
            trace["reconciles"] += 1

    class FakeTray:
        available = True

        def __init__(self, desktop):
            self.desktop = desktop

        def start(self):
            trace["tray_starts"] += 1

        def start_worker(self):
            self.desktop.auto_start_core()

        def shutdown(self):
            pass

    app.TrayController = FakeTray

    class TracedDesktop(app.DesktopShell):
        def __init__(self, **kwargs):
            trace["shells"] += 1
            super().__init__(
                **kwargs,
                preferences=PreferencesStore(),
                bridge=SimpleNamespace(),
                autostart=FakeAutostart(),
            )

        def auto_start_core(self):
            trace["auto_start_calls"] += 1
            # Default isolated preferences disable it; any Core call would fail.
            super().auto_start_core()

    app.DesktopShell = TracedDesktop
    desktop = None

    class Event:
        def __iadd__(self, handler):
            return self

    class FakeWindow:
        events = SimpleNamespace(before_show=Event(), closing=Event())

        def show(self):
            trace["activations"] += 1
            (directory / "activated").touch()

        def restore(self):
            pass

    def create_window(*args, **kwargs):
        nonlocal desktop
        trace["windows"] += 1
        desktop = kwargs["js_api"]._desktop
        return FakeWindow()

    def start_webview(worker, **kwargs):
        worker()
        if role != "primary":
            return
        app.logger.info("Isolated desktop smoke primary ready")
        ready = directory / "ready.tmp"
        ready.write_text(json.dumps(trace), encoding="utf-8")
        ready.replace(directory / "ready.json")
        deadline = time.monotonic() + 15
        while not (directory / "stop").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Isolated smoke host timed out")
            desktop.activate_pending()
            time.sleep(0.01)

    sys.modules["webview"] = SimpleNamespace(
        create_window=create_window, start=start_webview
    )
    app.frontend_url = lambda _: "file:///isolated-smoke.html"
    sys.argv = ["app.py", *(["--startup"] if role == "startup" else [])]
    app.main()
    print(json.dumps(trace))


if __name__ == "__main__":
    main()
