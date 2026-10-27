"""Shutdown contracts plus real Windows tests in disposable GUI processes."""

import asyncio
import importlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def gui(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "gui/pywebview/python"))
    return SimpleNamespace(**{name: importlib.import_module(name) for name in ("app", "bridge", "tray")})


def assert_processes_exited(pids):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32")
    open_process = kernel.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    wait = kernel.WaitForSingleObject
    wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait.restype = wintypes.DWORD
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    for pid in pids:
        handle = open_process(0x100000, False, pid)  # SYNCHRONIZE, never terminate.
        if handle:
            try:
                assert wait(handle, 5000) == 0, f"Test WebView2 process {pid} remained alive"
            finally:
                close(handle)


def probe_command():
    frozen = os.environ.get("CHAT2LOCAL_SHUTDOWN_TEST_EXE")
    if frozen:
        executable = Path(frozen).resolve(strict=True)
        return [str(executable)]
    return [sys.executable, str(Path(__file__).parent / "fixtures/gui_shutdown_host.py")]


def test_shutdown_cancels_running_async_calls_without_core_stop(gui):
    entered, cancelled = threading.Event(), threading.Event()

    async def status():
        entered.set()
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.set()

    adapter = SimpleNamespace(runtime_status=status, runtime=SimpleNamespace(action=Mock()))
    bridge = gui.bridge.GuiBridge(adapter)
    results = []
    worker = threading.Thread(target=lambda: results.append(bridge.runtime_status()))
    worker.start()
    try:
        assert entered.wait(3)
        bridge._begin_shutdown()
        worker.join(3)
        assert not worker.is_alive() and cancelled.is_set()
        assert results == [{"ok": False, "error": {"message": "Desktop is exiting"}}]
        assert not bridge._calls
        assert not bridge.runtime_status()["ok"]
        adapter.runtime.action.assert_not_called()
    finally:
        bridge._begin_shutdown()
        worker.join(3)


def test_shutdown_race_before_coroutine_registration_is_cancelled(gui):
    bridge = gui.bridge.GuiBridge()
    ran = []

    async def operation():
        ran.append(True)

    def begin():
        bridge._begin_shutdown()
        return operation()

    assert bridge._call(begin) == {"ok": False, "error": {"message": "Desktop is exiting"}}
    assert not ran and not bridge._calls


def test_shutdown_cancels_executor_operation(gui):
    entered, cancelled = threading.Event(), threading.Event()

    async def status():
        return {"lifecycle": "Stopped", "core": None}

    async def action(*_):
        entered.set()
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.set()

    bridge = gui.bridge.GuiBridge(SimpleNamespace(
        runtime_status=status, runtime=SimpleNamespace(action=action)
    ))
    desktop = gui.app.DesktopShell(bridge=bridge)
    desktop.tray.start_worker()
    try:
        desktop.tray.submit("stop")
        assert entered.wait(3)
        desktop.exit()
        desktop.shutdown()
        assert cancelled.is_set()
        assert not bridge._calls
        assert all(not thread.is_alive() for thread in desktop.tray._operations._threads)
    finally:
        desktop.shutdown()


def test_confirmed_session_end_before_webview_skips_window_creation(gui, monkeypatch):
    desktop = gui.app.DesktopShell(autostart=Mock())
    desktop.tray.start = lambda: desktop.tray.session_ended()
    instance = Mock()
    instance.claim.return_value = True
    monkeypatch.setattr(gui.app, "DesktopShell", lambda **_: desktop)
    monkeypatch.setattr(gui.app, "SingleInstance", lambda: instance)
    monkeypatch.setattr(gui.app, "frontend_url", lambda _: "file:///probe.html")
    webview = Mock()
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(sys, "argv", ["app.py"])
    gui.app.main()
    assert desktop.exiting
    webview.create_window.assert_not_called()
    webview.start.assert_not_called()
    instance.close.assert_called_once()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native startup lifecycle")
def test_confirmed_session_end_before_show_closes_form(gui):
    winforms = pytest.importorskip("webview.platforms.winforms")
    desktop = gui.app.DesktopShell()
    desktop.exit()
    form = winforms.WinForms.Form()
    assert form.Handle.ToInt64()
    desktop.window = Mock(native=form)
    try:
        desktop.before_show()
        winforms.WinForms.Application.DoEvents()
        assert form.IsDisposed and desktop.exiting
    finally:
        form.Dispose()
        desktop.shutdown()


def test_exit_bypasses_blocked_status_and_finishes_worker(gui):
    entered = threading.Event()

    async def status():
        entered.set()
        await asyncio.sleep(3600)

    desktop = gui.app.DesktopShell(bridge=gui.bridge.GuiBridge(SimpleNamespace(runtime_status=status)))
    desktop.window = Mock()
    desktop.tray.start_worker()
    try:
        assert entered.wait(3)
        desktop.tray.submit("exit")
        assert desktop.exiting
        desktop.window.destroy.assert_called_once()
        desktop.shutdown()
        assert not desktop.tray._worker.is_alive()
        assert all(not thread.is_alive() for thread in desktop.tray._operations._threads)
    finally:
        desktop.shutdown()


@pytest.mark.parametrize("gui_launched", [True, False], ids=["gui-launched-core", "independent-core"])
def test_gui_exit_preserves_real_isolated_core(gui, tmp_path, monkeypatch, gui_launched):
    from chat2local.management.runtime import RuntimeClient
    from chat2local.runtime import config

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = config.AppConfig(browser={"enabled": False}, security={"allowed_roots": [str(workspace)]})
    (tmp_path / "config.yaml").write_text(
        f"browser:\n  enabled: false\nsecurity:\n  allowed_roots:\n    - {json.dumps(str(workspace))}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "load_config", lambda: settings)
    client = RuntimeClient(tmp_path, timeout=10)
    host = Path(__file__).parent / "fixtures/gui_shutdown_host.py"
    monkeypatch.setattr(client, "_args", lambda mode, root: [
        sys.executable, str(host), "--isolated-core", str(tmp_path),
        "--workspace", root, "--config", str(tmp_path / "config.yaml"),
        "--port", "0", "--no-web-ui",
    ])
    gui_client = client if gui_launched else RuntimeClient(tmp_path)
    bridge = gui.bridge.GuiBridge(SimpleNamespace(runtime_status=gui_client.status, runtime=gui_client))
    desktop = gui.app.DesktopShell(bridge=bridge)
    try:
        snapshot = (bridge.start_core("standalone", str(workspace))["result"] if gui_launched
                    else asyncio.run(client.action("start", "standalone", str(workspace))))
        assert snapshot["lifecycle"] == "Running"
        pid = snapshot["core"]["pid"]
        desktop.exit()
        desktop.shutdown()
        after = asyncio.run(RuntimeClient(tmp_path).status())
        assert after["lifecycle"] == "Running" and after["core"]["pid"] == pid
        assert client._child.poll() is None
    finally:
        # This client verifies the unique test runtime descriptor before stop.
        # Only the Popen handle owned by this test may be terminated on failure.
        if client._child is not None and client._child.poll() is None:
            try:
                asyncio.run(client.action("stop"))
                client._child.wait(timeout=5)
            finally:
                if client._child.poll() is None:
                    client._child.kill()
                    client._child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Real isolated Windows GUI processes")
@pytest.mark.parametrize(
    "visibility,target,flags,scenario",
    [
        ("visible", "main", 0, "normal"),
        ("hidden", "tray", 0, "normal"),
        ("hidden", "menu", 0x80000000, "normal"),
        ("visible", "exit", 0, "blocked-status"),
        ("hidden", "menu", 0, "blocked-status"),
        ("hidden", "close-menu", 0, "normal"),
        ("visible", "menu", 0, "open-menu"),
        ("hidden", "main", 0x80000000, "open-menu"),
        ("visible", "exit", 0, "open-menu"),
    ],
)
def test_real_gui_session_lifecycle(tmp_path, visibility, target, flags, scenario):
    pytest.importorskip("webview")
    pytest.importorskip("pystray")
    result = subprocess.run(
        [*probe_command(), str(tmp_path), visibility, target, str(flags), scenario],
        capture_output=True, timeout=35, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert (tmp_path / "result.json").is_file(), result.stderr.decode(errors="replace")
    report = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result.returncode == 0, (report, result.stderr.decode(errors="replace"))
    assert not report["errors"] and report["resources_released"]
    assert report["cancelled_session_usable"]
    assert {query["role"] for query in report["queries"]} >= {"main", "tray", "menu"}
    assert all(query["result"] != 0 for query in report["queries"])
    assert report["exit_notification_seconds"] < 2
    assert_processes_exited(report["browser_pids"])


@pytest.mark.skipif(sys.platform != "win32", reason="Real isolated Windows GUI processes")
def test_real_gui_crash_then_reacquire(tmp_path):
    pytest.importorskip("webview")
    pytest.importorskip("pystray")
    command = [*probe_command(), str(tmp_path), "hidden", "exit", "0"]
    for scenario, expected in [("crash", 23), ("normal", 0), ("normal", 0)]:
        result = subprocess.run(
            [*command, scenario], capture_output=True, timeout=35,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == expected, result.stderr.decode(errors="replace")
        report = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
        assert not report["errors"]
        if scenario == "normal":
            assert report["resources_released"]
        assert_processes_exited(report["browser_pids"])


@pytest.mark.skipif(sys.platform != "win32", reason="Real Windows tray resources")
def test_native_tray_repeated_start_failure_and_cleanup(gui, monkeypatch):
    pytest.importorskip("pystray")
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    get_class = user32.GetClassInfoExW
    get_class.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_void_p]
    get_class.restype = wintypes.BOOL
    is_window = user32.IsWindow
    is_window.argtypes = [wintypes.HWND]
    is_window.restype = wintypes.BOOL
    from pystray._util import win32

    for failure in (None, "second-window", "setup", None):
        desktop = gui.app.DesktopShell()
        tray = desktop.tray
        icon = gui.tray.create_icon(tray)
        class_name = f"{icon.name}{id(icon)}SystemTrayIcon"
        info = win32.WNDCLASSEX(cbSize=ctypes.sizeof(win32.WNDCLASSEX))
        assert get_class(win32.GetModuleHandle(None), class_name, ctypes.byref(info))
        tray.icon_factory = lambda _: icon
        if failure == "second-window":
            create = icon._create_window
            calls = []

            def create_window(atom):
                if calls:
                    raise OSError("Injected second-window failure")
                hwnd = create(atom)
                calls.append(hwnd)
                return hwnd

            monkeypatch.setattr(icon, "_create_window", create_window)
        elif failure == "setup":
            monkeypatch.setattr(tray, "_setup", Mock(side_effect=OSError("Injected setup failure")))
        assert tray.start() is (failure is None)
        tray.shutdown()
        assert not tray._loop.is_alive()
        assert not icon._running and icon._icon_handle is None
        assert not any(is_window(hwnd) for hwnd in (icon._hwnd, icon._menu_hwnd) if hwnd)
        info = win32.WNDCLASSEX(cbSize=ctypes.sizeof(win32.WNDCLASSEX))
        assert not get_class(win32.GetModuleHandle(None), class_name, ctypes.byref(info))
        assert all(value is not icon for value in icon._HWND_TO_ICON.values())
