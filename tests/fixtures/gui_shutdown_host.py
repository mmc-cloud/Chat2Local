"""Real, isolated WinForms/WebView2 + pystray shutdown probe (also freezable).

Never broadcasts a message or calls a Windows shutdown API. All HWNDs are
checked against this process. Registry, user-data and Core IPC are isolated.
"""

import asyncio
import ctypes
import json
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace


def browser_children():
    """Inventory this probe's WebView2 children; never touches another GUI."""
    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    snapshot.restype = wintypes.HANDLE
    first, following = kernel.Process32FirstW, kernel.Process32NextW
    for function in (first, following):
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        function.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = snapshot(2, 0)
    assert handle != ctypes.c_void_p(-1).value
    entries = []
    try:
        entry = Entry(size=ctypes.sizeof(Entry))
        found = first(handle, ctypes.byref(entry))
        while found:
            entries.append((entry.pid, entry.parent, entry.name))
            found = following(handle, ctypes.byref(entry))
    finally:
        close(handle)
    descendants = {os.getpid()}
    while True:
        children = {pid for pid, parent, _ in entries if parent in descendants}
        if children <= descendants:
            break
        descendants.update(children)
    return [pid for pid, _, name in entries if pid in descendants and name.lower() == "msedgewebview2.exe"]


def main():
    directory, visibility, target, flags, scenario = sys.argv[1:]
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gui/pywebview/python"))
    import app
    import launcher
    import webview
    from bridge import GuiBridge
    from preferences import PreferencesStore
    from single_instance import SingleInstance, WindowsOwnership

    trace = {"frozen": bool(getattr(sys, "frozen", False)), "queries": [], "errors": []}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    send = user32.SendMessageTimeoutW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                     wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    send.restype = ctypes.c_ssize_t
    get_pid = user32.GetWindowThreadProcessId
    get_pid.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    get_pid.restype = wintypes.DWORD
    is_window = user32.IsWindow
    is_window.argtypes = [wintypes.HWND]
    is_window.restype = wintypes.BOOL
    visible = user32.IsWindowVisible
    visible.argtypes = [wintypes.HWND]
    visible.restype = wintypes.BOOL
    get_class = user32.GetClassNameW
    get_class.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    get_class.restype = ctypes.c_int

    def wait_for(predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError("Isolated shutdown probe timed out")
            time.sleep(0.01)

    def message(hwnd, code, wparam=0, lparam=0):
        pid = wintypes.DWORD()
        assert get_pid(hwnd, ctypes.byref(pid)) and pid.value == os.getpid()
        result = ctypes.c_size_t()
        assert send(hwnd, code, wparam, lparam, 2, 3000, ctypes.byref(result)), ctypes.get_last_error()
        return result.value

    app.core_config.user_data_directory = lambda: directory
    store = PreferencesStore(directory / "gui/preferences.json")
    store.save(store.candidate({"silent_login_start": True}))
    blocking = threading.Event()
    entered = threading.Event()
    cancelled = threading.Event()

    async def status():
        if blocking.is_set():
            entered.set()
            try:
                await asyncio.sleep(3600)
            finally:
                cancelled.set()
        return {"lifecycle": "Stopped", "core": None}

    bridge = GuiBridge(SimpleNamespace(runtime_status=status), store)
    desktop = app.DesktopShell(
        startup=visibility == "hidden", preferences=store, bridge=bridge,
        autostart=SimpleNamespace(reconcile=lambda _: None),
    )
    instance = SingleInstance(
        directory / "gui", ownership=WindowsOwnership(
            name=rf"Local\Chat2Local.ShutdownTest.{directory.name}"
        ),
    )
    app.DesktopShell = lambda **_: desktop
    app.SingleInstance = lambda: instance
    index = directory / "probe.html"
    index.write_text("<html><body>Isolated Chat2Local shutdown test</body></html>", encoding="utf-8")
    app.frontend_url = lambda _: index.as_uri()
    original_create = webview.create_window

    def create_window(*args, **kwargs):
        return original_create("Chat2Local isolated shutdown test", **kwargs)

    webview.create_window = create_window
    handles = {}

    def drive():
        try:
            wait_for(lambda: desktop.window is not None and desktop.window.events.loaded.is_set())
            handles.update(main=desktop.window.native.Handle.ToInt64(),
                           tray=desktop.tray.icon._hwnd, menu=desktop.tray.icon._menu_hwnd)
            trace["initial_visible"] = bool(visible(handles["main"]))
            assert trace["initial_visible"] is (visibility == "visible")
            # WM_CLOSE before a session query must still use the tray policy.
            message(handles["main"], 0x10)
            assert not visible(handles["main"]) and not desktop.exiting

            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            enum_windows = user32.EnumWindows
            enum_windows.argtypes = [callback_type, wintypes.LPARAM]
            enum_windows.restype = wintypes.BOOL
            owned = []

            @callback_type
            def collect(hwnd, _):
                pid = wintypes.DWORD()
                get_pid(hwnd, ctypes.byref(pid))
                if pid.value == os.getpid():
                    owned.append(hwnd)
                return True

            assert enum_windows(collect, 0)
            assert all(hwnd in owned for hwnd in handles.values())
            trace["top_level_window_count"] = len(owned)
            for _ in range(2):
                for hwnd in owned:
                    result = message(hwnd, 0x11, 0, int(flags))
                    name = ctypes.create_unicode_buffer(256)
                    assert get_class(hwnd, name, len(name))
                    trace["queries"].append({
                        "role": next((k for k, v in handles.items() if v == hwnd), "auxiliary"),
                        "class": name.value, "result": result,
                    })
                    assert result != 0
                    if hwnd in handles.values():
                        assert result == 1
                for hwnd in owned:
                    message(hwnd, 0x16, 0, int(flags))
                assert not desktop.exiting and desktop.tray.available
                assert desktop.request_activation()
                wait_for(lambda: bool(visible(handles["main"])))
                message(handles["main"], 0x112, 0xF060)  # X and Alt+F4 / SC_CLOSE
                assert not visible(handles["main"]) and not desktop.exiting
            trace["cancelled_session_usable"] = True
            trace["browser_pids"] = browser_children()
            if scenario == "crash":
                (directory / "result.json").write_text(json.dumps(trace), encoding="utf-8")
                os._exit(23)  # Only this disposable test process.
            if scenario == "blocked-status":
                blocking.set()
                wait_for(entered.is_set)
            if scenario == "open-menu":
                post = user32.PostMessageW
                post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                post.restype = wintypes.BOOL
                assert post(handles["tray"], 0x40B, 0, 0x205)  # pystray WM_NOTIFY / right click

                def menu_open():
                    owned.clear()
                    enum_windows(collect, 0)
                    for hwnd in owned:
                        name = ctypes.create_unicode_buffer(256)
                        get_class(hwnd, name, len(name))
                        if name.value == "#32768" and visible(hwnd):
                            return True
                    return False

                wait_for(menu_open)
                trace["open_menu"] = True
                for hwnd in tuple(owned):
                    assert message(hwnd, 0x11, 0, int(flags)) != 0
                    message(hwnd, 0x16, 0, int(flags))
                assert menu_open() and not desktop.exiting
                trace["popup_cancel_usable"] = True
            started = time.monotonic()
            if target == "exit":
                desktop.tray.submit("exit")
            elif target == "close-menu":
                message(handles["menu"], 0x10)
            else:
                message(handles[target], 0x16, 1, int(flags))
            wait_for(lambda: desktop.exiting)
            trace["exit_notification_seconds"] = time.monotonic() - started
        except BaseException:
            trace["errors"].append(traceback.format_exc())
            desktop.exit()

    driver = threading.Thread(target=drive, name="shutdown-test-driver", daemon=True)
    original_start = webview.start

    def start(worker, **kwargs):
        def run():
            worker()
            driver.start()
        return original_start(run, **kwargs)

    webview.start = start
    sys.argv = [sys.argv[0]]
    try:
        launcher.main()
        driver.join(5)
        assert not driver.is_alive()
        assert not desktop.tray._loop.is_alive()
        assert not desktop.tray._worker.is_alive()
        assert all(not thread.is_alive() for thread in desktop.tray._operations._threads)
        assert not instance._thread.is_alive()
        assert not desktop.tray.icon._running
        assert desktop.tray.icon._icon_handle is None
        assert not any(is_window(hwnd) for hwnd in handles.values())
        assert not list((directory / "gui").glob("instance-*.json"))
        if scenario == "blocked-status":
            assert cancelled.is_set()
        trace["resources_released"] = True
    except BaseException:
        trace["errors"].append(traceback.format_exc())
    (directory / "result.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")
    assert not trace["errors"], trace["errors"]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--isolated-core":
        directory = Path(sys.argv[2]).resolve()
        from chat2local.runtime import config

        config.user_data_directory = lambda: directory
        config.DEFAULT_CONFIG_PATH = directory / "config.yaml"
        if not getattr(sys, "frozen", False):
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gui/pywebview/python"))
        import launcher

        sys.argv = [sys.argv[0], "--core", *sys.argv[3:]]
        launcher.main()
    else:
        main()
