"""Desktop contracts without a real WebView, Notification Area or HKCU writes."""

import json
import subprocess
import sys
import threading
from dataclasses import asdict, replace
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "gui/pywebview/python")
    )
    return SimpleNamespace(
        **{
            name: import_module(name)
            for name in ("preferences", "autostart", "app", "bridge", "tray")
        }
    )


def running(workspace):
    return {
        "lifecycle": "Running",
        "core": {"mode": "agent", "workspace": str(workspace), "state": "running"},
    }


@pytest.fixture
def desktop(modules, tmp_path):
    state = {"snapshot": {"lifecycle": "Stopped", "core": None}, "actions": []}

    async def status():
        return dict(state["snapshot"])

    async def action(name, *context):
        state["actions"].append((name, *context))
        if name == "start":
            state["snapshot"] = {
                "lifecycle": "Running",
                "core": {"mode": context[0], "workspace": context[1]},
            }
        elif name == "stop":
            state["snapshot"] = {"lifecycle": "Stopped", "core": None}
        return dict(state["snapshot"])

    preferences = modules.preferences.PreferencesStore(
        tmp_path / "gui/preferences.json"
    )
    bridge = modules.bridge.GuiBridge(
        SimpleNamespace(runtime_status=status, runtime=SimpleNamespace(action=action)),
        preferences,
    )
    shell = modules.app.DesktopShell(preferences=preferences, bridge=bridge)
    shell.window = Mock()
    shell.tray = SimpleNamespace(available=True, shutdown=Mock())
    shell.test_state = state
    return shell


def test_preferences_defaults_missing_and_null(modules, tmp_path):
    store = modules.preferences.PreferencesStore(tmp_path / "missing.json")
    assert asdict(store.current) == {
        "schema_version": 1,
        "launch_at_login": False,
        "silent_login_start": False,
        "close_behavior": "tray",
        "auto_start_core": False,
        "startup_mode": None,
        "startup_workspace": None,
    }
    assert not store.path.exists()
    store.save(store.current)
    assert modules.preferences.PreferencesStore(store.path).current == store.current
    assert (
        json.loads(store.path.read_text(encoding="utf-8"))["startup_workspace"] is None
    )


def test_preferences_save_utf8_and_atomic(modules, tmp_path, monkeypatch):
    store = modules.preferences.PreferencesStore(tmp_path / "preferences.json")
    store.save(store.current)
    before = store.path.read_bytes()
    original = modules.preferences.os.replace
    replacements = []

    def replace_file(source, target):
        assert Path(source).parent == store.path.parent
        assert store.path.read_bytes() == before
        assert json.loads(Path(source).read_text(encoding="utf-8"))[
            "startup_workspace"
        ] == str(tmp_path / "工作区")
        replacements.append((source, target))
        original(source, target)

    monkeypatch.setattr(modules.preferences.os, "replace", replace_file)
    expected = replace(
        store.current,
        auto_start_core=True,
        startup_mode="hub",
        startup_workspace=str(tmp_path / "工作区"),
    )
    store.save(expected)
    assert (
        replacements
        and modules.preferences.PreferencesStore(store.path).current == expected
    )
    assert "工作区" in store.path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob(".preferences-*.tmp"))


def test_atomic_save_failure_keeps_previous_state(modules, tmp_path, monkeypatch):
    store = modules.preferences.PreferencesStore(tmp_path / "preferences.json")
    store.save(store.current)
    before = store.path.read_bytes()
    monkeypatch.setattr(
        modules.preferences.os, "replace", Mock(side_effect=PermissionError("denied"))
    )
    with pytest.raises(OSError):
        store.save(replace(store.current, close_behavior="exit"))
    assert store.current.close_behavior == "tray"
    assert store.path.read_bytes() == before
    assert not list(tmp_path.glob(".preferences-*.tmp"))


@pytest.mark.parametrize(
    "raw",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"close_behavior": "minimize"},
        {"close_behavior": None},
        {"launch_at_login": 1},
        {"silent_login_start": "true"},
        {"auto_start_core": None},
        {"startup_mode": "service"},
        {"startup_mode": []},
        {"startup_workspace": "relative"},
        {"startup_workspace": ""},
        {"startup_workspace": 4},
        {"unexpected": True},
        [],
        None,
    ],
)
def test_invalid_preferences_fall_back(modules, tmp_path, caplog, raw):
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert (
        modules.preferences.PreferencesStore(path).current
        == modules.preferences.DesktopPreferences()
    )
    assert "using defaults" in caplog.text


@pytest.mark.parametrize("contents", [b"broken JSON", b"\xff", b'{"launch_at_login":'])
def test_corrupt_preferences_fall_back(modules, tmp_path, caplog, contents):
    path = tmp_path / "preferences.json"
    path.write_bytes(contents)
    assert (
        modules.preferences.PreferencesStore(path).current
        == modules.preferences.DesktopPreferences()
    )
    assert "using defaults" in caplog.text


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {"OtherApp": ("keep me", self.REG_SZ)}
        self.writes = []
        self.deletes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def OpenKey(self, hive, key, reserved, access):
        assert (
            hive == "HKCU" and key == r"Software\Microsoft\Windows\CurrentVersion\Run"
        )
        return self

    CreateKeyEx = OpenKey

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        return self.values[name]

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = (value, kind)
        self.writes.append((name, value))

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        self.deletes.append(name)
        del self.values[name]


@pytest.fixture
def registry(modules, monkeypatch):
    registry = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(
        modules.autostart,
        "sys",
        SimpleNamespace(
            platform="win32", executable=r"C:\Python with spaces\python.exe"
        ),
    )
    return registry


def test_autostart_enable_disable_idempotent(modules, registry):
    autostart = modules.autostart.WindowsAutostart()
    assert autostart.read() is None
    autostart.set_enabled(True)
    assert registry.values["Chat2Local"] == (
        modules.autostart.startup_command(),
        registry.REG_SZ,
    )
    autostart.set_enabled(True)
    assert len(registry.writes) == 1
    autostart.set_enabled(False)
    autostart.set_enabled(False)
    assert "Chat2Local" not in registry.values
    assert registry.values["OtherApp"] == ("keep me", registry.REG_SZ)


@pytest.mark.parametrize(
    "enabled,initial,writes,deletes",
    [
        (True, "missing", 1, 0),
        (True, "old", 1, 0),
        (True, "current", 0, 0),
        (False, "old", 0, 1),
        (False, "missing", 0, 0),
    ],
)
def test_autostart_reconcile_matches_intent_and_leaves_other_entries(
    modules, registry, enabled, initial, writes, deletes
):
    desired = (modules.autostart.startup_command(), registry.REG_SZ)
    if initial != "missing":
        registry.values["Chat2Local"] = (
            desired if initial == "current" else ("old command", registry.REG_SZ)
        )
    autostart = modules.autostart.WindowsAutostart()
    autostart.reconcile(enabled)
    assert registry.values.get("Chat2Local") == (desired if enabled else None)
    assert len(registry.writes) == writes and len(registry.deletes) == deletes
    assert registry.values["OtherApp"] == ("keep me", registry.REG_SZ)
    autostart.reconcile(enabled)
    assert len(registry.writes) == writes and len(registry.deletes) == deletes


@pytest.mark.parametrize("frozen", [False, True])
def test_reconcile_migrates_old_python_command_to_pythonw_or_frozen(
    modules, registry, tmp_path, monkeypatch, frozen
):
    python = tmp_path / "old python" / "python.exe"
    python.parent.mkdir()
    pythonw = python.with_name("pythonw.exe")
    pythonw.touch()
    source = tmp_path / "source with spaces/gui/autostart.py"
    monkeypatch.setattr(modules.autostart, "__file__", str(source))
    monkeypatch.setattr(modules.autostart.sys, "executable", str(python))
    old_args = [str(python), str(source.resolve().with_name("app.py")), "--startup"]
    registry.values["Chat2Local"] = (subprocess.list2cmdline(old_args), registry.REG_SZ)
    expected = [str(pythonw), old_args[1], "--startup"]
    if frozen:
        # Future source->EXE migration starts from an already current source command.
        registry.values["Chat2Local"] = (
            subprocess.list2cmdline(expected),
            registry.REG_SZ,
        )
        executable = tmp_path / "new release/Chat2Local.exe"
        monkeypatch.setattr(modules.autostart.sys, "executable", str(executable))
        monkeypatch.setattr(modules.autostart.sys, "frozen", True, raising=False)
        expected = [str(executable), "--startup"]
    modules.autostart.WindowsAutostart().reconcile(True)
    assert registry.values["Chat2Local"] == (
        subprocess.list2cmdline(expected),
        registry.REG_SZ,
    )
    assert len(registry.writes) == 1
    assert registry.values["OtherApp"] == ("keep me", registry.REG_SZ)


def test_non_windows_reconcile_is_a_noop(modules, monkeypatch):
    monkeypatch.setattr(modules.autostart, "sys", SimpleNamespace(platform="linux"))
    registry = Mock(side_effect=AssertionError("No Windows registry on this platform"))
    autostart = modules.autostart.WindowsAutostart()
    monkeypatch.setattr(autostart, "_registry", registry)
    autostart.reconcile(True)
    autostart.reconcile(False)
    registry.assert_not_called()


@pytest.mark.parametrize("enabled", [True, False])
def test_desktop_reconcile_never_rewrites_preferences(
    desktop, modules, registry, enabled, monkeypatch
):
    desktop.preferences.save(
        replace(desktop.preferences.current, launch_at_login=enabled)
    )
    before = desktop.preferences.path.read_bytes()
    desktop.autostart = modules.autostart.WindowsAutostart()
    save = Mock(side_effect=AssertionError("Reconcile must not save preferences"))
    monkeypatch.setattr(desktop.preferences, "save", save)
    desktop.reconcile_login_startup()
    assert desktop.preferences.path.read_bytes() == before
    save.assert_not_called()
    assert ("Chat2Local" in registry.values) is enabled


def test_invalid_preferences_skip_registry_reconcile_and_report_error(
    modules, tmp_path, registry
):
    path = tmp_path / "preferences.json"
    path.write_text("{broken", encoding="utf-8")
    store = modules.preferences.PreferencesStore(path)
    assert store.load_state == "invalid"
    registry.values["Chat2Local"] = ("existing startup command", registry.REG_SZ)
    desktop = modules.app.DesktopShell(
        preferences=store, autostart=modules.autostart.WindowsAutostart()
    )
    desktop.reconcile_login_startup()
    assert registry.values["Chat2Local"] == (
        "existing startup command",
        registry.REG_SZ,
    )
    assert registry.writes == [] and registry.deletes == []
    assert (
        desktop.error
        == "Desktop preferences invalid or unreadable; Windows login startup was not changed"
    )


def test_missing_preferences_remain_authoritative_defaults_for_reconcile(
    modules, tmp_path, registry
):
    store = modules.preferences.PreferencesStore(tmp_path / "missing.json")
    assert store.load_state == "missing"
    registry.values["Chat2Local"] = ("stale startup command", registry.REG_SZ)
    desktop = modules.app.DesktopShell(
        preferences=store, autostart=modules.autostart.WindowsAutostart()
    )
    desktop.reconcile_login_startup()
    assert "Chat2Local" not in registry.values
    assert registry.deletes == ["Chat2Local"]


@pytest.mark.parametrize(
    "frozen,pythonw_exists", [(True, True), (False, True), (False, False)]
)
def test_windows_startup_command_spaces_and_explicit_startup(
    modules, registry, monkeypatch, tmp_path, frozen, pythonw_exists, caplog
):
    source = tmp_path / "source with spaces/gui/autostart.py"
    executable = tmp_path / "Python with spaces" / "python.exe"
    executable.parent.mkdir()
    pythonw = executable.with_name("pythonw.exe")
    if pythonw_exists:
        pythonw.touch()
    monkeypatch.setattr(modules.autostart.sys, "executable", str(executable))
    monkeypatch.setattr(modules.autostart, "__file__", str(source))
    monkeypatch.setattr(modules.autostart.sys, "frozen", frozen, raising=False)
    command = modules.autostart.startup_command()
    args = [str(pythonw if pythonw_exists and not frozen else executable)]
    if not frozen:
        args.append(str(source.resolve().with_name("app.py")))
    args.append("--startup")
    assert command == subprocess.list2cmdline(args)
    assert command.startswith('"' + args[0] + '"')
    assert ("fallback may display a console" in caplog.text) is (
        not frozen and not pythonw_exists
    )
    # Validate quoting with the Windows parser, independent of the builder.
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        parser = ctypes.windll.shell32.CommandLineToArgvW
        monkeypatch.setattr(
            parser, "argtypes", [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        )
        monkeypatch.setattr(parser, "restype", ctypes.POINTER(wintypes.LPWSTR))
        count = ctypes.c_int()
        parsed = parser(command, ctypes.byref(count))
        try:
            assert list(parsed[: count.value]) == args
        finally:
            monkeypatch.setattr(
                ctypes.windll.kernel32.LocalFree, "argtypes", [ctypes.c_void_p]
            )
            ctypes.windll.kernel32.LocalFree(parsed)


def test_non_windows_unsupported_no_winreg_import(modules, monkeypatch):
    monkeypatch.setattr(modules.autostart, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setitem(sys.modules, "winreg", None)
    autostart = modules.autostart.WindowsAutostart()
    assert not autostart.supported
    with pytest.raises(ValueError, match="not supported"):
        autostart.set_enabled(True)


def test_non_windows_startup_keeps_interpreter(modules, monkeypatch):
    monkeypatch.setattr(
        modules.autostart,
        "sys",
        SimpleNamespace(platform="linux", executable="/usr/bin/python3"),
    )
    monkeypatch.setattr(
        Path, "is_file", Mock(side_effect=AssertionError("No pythonw probe"))
    )
    assert modules.autostart.desktop_executable() == "/usr/bin/python3"


@pytest.mark.parametrize(
    "component,diagnostic",
    [
        ("bridge", "Internal GUI/Core error"),
        ("preferences", "using defaults"),
        ("autostart", "Could not inspect pythonw.exe"),
        ("app", "Could not update Windows login startup"),
        ("tray", "Desktop Core operation failed"),
        ("context", "Could not save confirmed Core startup context"),
        ("formatter", "Internal GUI/Core error"),
    ],
)
def test_real_desktop_log_never_persists_unknown_exception_values(
    modules, desktop, isolated_user_data, monkeypatch, component, diagnostic
):
    import logging

    secret = "https://user:SUPER-SECRET@example.com TOKEN-123456"

    def fail():
        try:
            raise ValueError(secret)
        except ValueError as cause:
            raise RuntimeError(secret) from cause

    with modules.app.desktop_logging():
        if component == "bridge":
            assert not desktop.bridge._call(fail)["ok"]
        elif component == "preferences":
            path = isolated_user_data / "gui/preferences.json"
            path.write_text(json.dumps({"close_behavior": secret}))
            modules.preferences.PreferencesStore(path)
        elif component == "autostart":
            monkeypatch.setattr(
                modules.autostart,
                "sys",
                SimpleNamespace(platform="win32", executable="C:/python.exe"),
            )
            with monkeypatch.context() as patch:
                patch.setattr(Path, "is_file", lambda _: fail())

                # Probe catches system OSError; its value is also untrusted.
                def inaccessible(_):
                    try:
                        fail()
                    except RuntimeError as error:
                        raise OSError(secret) from error

                patch.setattr(Path, "is_file", inaccessible)
                assert modules.autostart.desktop_executable() == "C:/python.exe"
        elif component == "app":

            def system_error(*args):
                try:
                    fail()
                except RuntimeError as error:
                    raise OSError(secret) from error

            desktop.autostart = SimpleNamespace(
                read=lambda: None, set_enabled=system_error
            )
            with pytest.raises(modules.app.ManagementError):
                desktop.save_preferences({"launch_at_login": True})
        elif component == "tray":
            controller = modules.tray.TrayController(desktop, platform="win32")
            controller._operate(fail)
        elif component == "context":
            monkeypatch.setattr(
                desktop.preferences, "remember_running", lambda _: fail()
            )
            desktop.bridge._observe({"ok": True, "result": running(isolated_user_data)})
        else:
            try:
                fail()
            except RuntimeError:
                record = logging.getLogger("chat2local.desktop.test").makeRecord(
                    "chat2local.desktop.test",
                    logging.ERROR,
                    __file__,
                    1,
                    "Internal GUI/Core error",
                    (),
                    sys.exc_info(),
                    sinfo=secret,
                )
                # A different formatter may already have cached an unsafe traceback.
                record.exc_text = secret
                logging.getLogger("chat2local.desktop.test").handle(record)
    contents = (isolated_user_data / "gui/desktop.log").read_text(encoding="utf-8")
    assert "SUPER-SECRET" not in contents
    assert "TOKEN-123456" not in contents
    assert diagnostic in contents
    assert (
        "RuntimeError"
        if component not in ("preferences", "autostart", "app")
        else "ValueError"
        if component == "preferences"
        else "OSError"
    ) in contents
    assert "traceback:" in contents


def test_raw_management_error_is_displayed_but_not_persisted(
    modules, desktop, isolated_user_data
):
    message = "https://user:SUPER-SECRET@example.com TOKEN-123456"
    with modules.app.desktop_logging():
        desktop.report_error(message)
    assert desktop.error == message
    contents = (isolated_user_data / "gui/desktop.log").read_text(encoding="utf-8")
    assert "SUPER-SECRET" not in contents and "TOKEN-123456" not in contents
    assert "Desktop error reported to GUI" in contents


@pytest.mark.parametrize(
    "behavior,available,hide,cancel",
    [
        ("tray", True, True, True),
        ("exit", True, False, False),
        ("tray", False, False, False),
    ],
)
def test_close_behavior(desktop, behavior, available, hide, cancel):
    desktop.preferences.current = replace(
        desktop.preferences.current, close_behavior=behavior
    )
    desktop.tray.available = available
    assert desktop.on_closing() is (not cancel)
    assert bool(desktop.window.hide.call_count) is hide
    assert desktop.exiting is (not cancel)
    assert desktop.test_state["actions"] == []


def test_explicit_exit_overrides_hide_preference(desktop):
    desktop.exit()
    desktop.window.destroy.assert_called_once()
    assert desktop.on_closing() is True
    desktop.window.hide.assert_not_called()
    desktop.shutdown()
    desktop.tray.shutdown.assert_called_once()
    assert not desktop.test_state["actions"]


@pytest.mark.parametrize(
    "startup,silent,tray,hidden",
    [
        (False, True, True, False),
        (True, False, True, False),
        (True, True, True, True),
        (True, True, False, False),
    ],
)
def test_silent_startup_only_for_login_with_tray(
    desktop, startup, silent, tray, hidden
):
    desktop.startup = startup
    desktop.preferences.current = replace(
        desktop.preferences.current, silent_login_start=silent
    )
    desktop.tray.available = tray
    assert desktop.hidden_start is hidden
    desktop.before_show()
    assert desktop.window.hidden is hidden
    desktop.window.hide.assert_not_called()


def test_tray_failure_restores_window(desktop):
    desktop.tray.available = False
    desktop.tray_failed()
    assert desktop.window.method_calls == [
        (("show"), (), {}),
        (("restore"), (), {}),
        (("show"), (), {}),
    ]
    assert "Tray unavailable" in desktop.error


def test_auto_start_disabled_does_not_query_or_act(desktop):
    desktop.bridge.runtime_status = Mock(side_effect=AssertionError("disabled"))
    desktop.auto_start_core()
    assert not desktop.test_state["actions"]


@pytest.mark.parametrize(
    "lifecycle", ["Running", "Starting", "Stopping", "Error", "Stale"]
)
def test_auto_start_no_duplicate_or_repair(desktop, tmp_path, lifecycle):
    desktop.preferences.current = replace(
        desktop.preferences.current,
        auto_start_core=True,
        startup_mode="hub",
        startup_workspace=str(tmp_path),
    )
    desktop.test_state["snapshot"] = (
        running(tmp_path)
        if lifecycle == "Running"
        else {"lifecycle": lifecycle, "core": None}
    )
    desktop.auto_start_core()
    assert not desktop.test_state["actions"]


@pytest.mark.parametrize(
    "mode,workspace", [(None, None), ("agent", None), (None, "valid")]
)
def test_auto_start_incomplete_context_skips(
    desktop, tmp_path, mode, workspace, caplog
):
    desktop.preferences.current = replace(
        desktop.preferences.current,
        auto_start_core=True,
        startup_mode=mode,
        startup_workspace=str(tmp_path) if workspace else None,
    )
    desktop.auto_start_core()
    assert not desktop.test_state["actions"]
    assert "no confirmed mode/workspace" in caplog.text


def test_auto_start_stopped_valid_context_starts_once(desktop, tmp_path):
    desktop.preferences.current = replace(
        desktop.preferences.current,
        auto_start_core=True,
        startup_mode="hub",
        startup_workspace=str(tmp_path),
    )
    desktop.auto_start_core()
    desktop.auto_start_core()
    assert desktop.test_state["actions"] == [("start", "hub", str(tmp_path))]


def test_manual_start_and_discovered_running_remember_confirmed_context(
    desktop, tmp_path
):
    result = desktop.bridge.start_core("hub", str(tmp_path))
    assert result["ok"]
    assert desktop.preferences.current.startup_mode == "hub"
    desktop.test_state["snapshot"] = running(tmp_path / "another")
    assert desktop.bridge.runtime_status()["ok"]
    assert desktop.preferences.current.startup_mode == "agent"
    assert desktop.preferences.current.startup_workspace == str(tmp_path / "another")


def test_failed_manual_start_does_not_save_context(desktop, tmp_path):
    from chat2local.management.runtime import ManagementError

    desktop.bridge._adapter.runtime.action = Mock(
        side_effect=ManagementError("Invalid Core mode")
    )
    assert not desktop.bridge.start_core("bad", str(tmp_path))["ok"]
    assert desktop.preferences.current.startup_mode is None
    assert not desktop.preferences.path.exists()


def test_context_save_failure_does_not_report_successful_core_start_as_failure(
    desktop, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        desktop.preferences, "save", Mock(side_effect=PermissionError("denied"))
    )
    assert desktop.bridge.start_core("hub", str(tmp_path))["ok"]
    assert "Could not save Core startup context" in desktop.error


def test_desktop_preferences_api_is_independent_and_preserves_context(
    desktop, tmp_path, registry
):
    desktop.test_state["snapshot"] = running(tmp_path)
    desktop.bridge.runtime_status()
    result = desktop.bridge.save_desktop_preferences(
        {"launch_at_login": True, "silent_login_start": True}
    )
    assert result["ok"]
    assert result["result"]["preferences"]["launch_at_login"] is True
    assert desktop.preferences.current.startup_workspace == str(tmp_path)
    assert not (tmp_path / "config.yaml").exists()
    assert not desktop.bridge.save_desktop_preferences({"startup_mode": "standalone"})[
        "ok"
    ]
    assert desktop.preferences.current.startup_mode == "agent"


def test_preferences_failure_rolls_back_own_registry_entry(
    desktop, registry, monkeypatch
):
    registry.values["Chat2Local"] = ("previous command", registry.REG_SZ)
    monkeypatch.setattr(
        desktop.preferences, "save", Mock(side_effect=PermissionError("denied"))
    )
    result = desktop.bridge.save_desktop_preferences({"launch_at_login": True})
    assert (
        not result["ok"]
        and "Could not save desktop preferences" in result["error"]["message"]
    )
    assert not desktop.preferences.current.launch_at_login
    assert registry.values["Chat2Local"] == ("previous command", registry.REG_SZ)
    assert registry.values["OtherApp"] == ("keep me", registry.REG_SZ)


def test_registry_failure_does_not_save_preferences(desktop, registry, monkeypatch):
    monkeypatch.setattr(
        registry, "SetValueEx", Mock(side_effect=PermissionError("denied"))
    )
    assert not desktop.bridge.save_desktop_preferences({"launch_at_login": True})["ok"]
    assert not desktop.preferences.current.launch_at_login
    assert not desktop.preferences.path.exists()


class FakeIcon:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.visible = False
        self.stopped = threading.Event()
        self.updates = 0

    def run(self, setup):
        if self.fail:
            raise RuntimeError("Native tray unavailable")
        setup(self)
        self.stopped.wait()

    def update_menu(self):
        self.updates += 1

    def stop(self):
        self.visible = False
        self.stopped.set()


@pytest.mark.parametrize(
    "command", ["open", "logs", "start", "stop", "restart", "exit"]
)
def test_tray_actions_use_bridge_and_existing_window(
    modules, desktop, tmp_path, command
):
    desktop.preferences.current = replace(
        desktop.preferences.current, startup_mode="hub", startup_workspace=str(tmp_path)
    )
    tray = modules.tray.TrayController(desktop)
    tray.perform(command)
    if command in ("start", "stop", "restart"):
        assert desktop.test_state["actions"][0][0] == command
    elif command == "exit":
        assert desktop.exiting
        desktop.window.destroy.assert_called_once()
    else:
        assert desktop.window.show.call_count == 2
        desktop.window.restore.assert_called_once()
        if command == "logs":
            assert desktop.bridge.take_desktop_navigation() == {
                "ok": True,
                "result": "Logs",
            }
            assert desktop.bridge.take_desktop_navigation()["result"] is None


@pytest.mark.parametrize("fail", [False, True])
def test_tray_start_shutdown_and_failure_cleanup(modules, desktop, fail):
    icon = FakeIcon(fail=fail)
    tray = modules.tray.TrayController(
        desktop, icon_factory=lambda _: icon, platform="win32"
    )
    assert tray.start() is (not fail)
    if not fail:
        tray.start_worker()
    tray.shutdown()
    assert not tray.available and not icon.visible
    assert not tray._loop.is_alive()
    assert tray._worker is None or not tray._worker.is_alive()
    tray.submit("start")
    assert tray._commands.empty()


def test_tray_menu_state_comes_from_management(modules, desktop, tmp_path):
    desktop.preferences.current = replace(
        desktop.preferences.current, startup_mode="hub", startup_workspace=str(tmp_path)
    )
    tray = modules.tray.TrayController(desktop)
    tray.refresh()
    assert tray.can_start and "hub · Stopped" in tray.status_text
    desktop.test_state["snapshot"] = running(tmp_path)
    tray.refresh()
    assert not tray.can_start and tray.status_text == "Core: agent · Running"


def test_tray_callbacks_do_not_block_on_runtime_work(modules, desktop):
    tray = modules.tray.TrayController(desktop)
    entered, release = threading.Event(), threading.Event()

    def blocking():
        entered.set()
        assert release.wait(5)
        return {"ok": True, "result": {"lifecycle": "Stopped", "core": None}}

    desktop.bridge.stop_core = blocking
    tray.start_worker()
    try:
        tray.submit("stop")
        assert entered.wait(5)
        tray.submit("restart")
        tray.submit("restart")
        assert tray._commands.qsize() == 1
    finally:
        tray._stopping.set()
        release.set()
        tray.shutdown()
    assert not tray._worker.is_alive()


def test_exit_stays_responsive_during_core_operation(modules, desktop):
    tray = modules.tray.TrayController(desktop)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def blocking():
        entered.set()
        assert release.wait(5)
        return {"ok": True, "result": {"lifecycle": "Stopped", "core": None}}

    desktop.bridge.stop_core = blocking
    desktop.window.destroy.side_effect = exited.set
    tray.start_worker()
    try:
        tray.submit("stop")
        assert entered.wait(5)
        tray.submit("restart")
        tray.submit("exit")
        assert exited.wait(3)
        assert desktop.exiting
        assert not release.is_set()
    finally:
        release.set()
        tray.shutdown()
    assert not tray._worker.is_alive()


def test_tray_failure_does_not_disable_auto_start(modules, desktop, tmp_path):
    desktop.preferences.current = replace(
        desktop.preferences.current,
        auto_start_core=True,
        startup_mode="hub",
        startup_workspace=str(tmp_path),
    )
    started = threading.Event()
    original = desktop.bridge.start_core

    def start(*args):
        result = original(*args)
        started.set()
        return result

    desktop.bridge.start_core = start
    tray = modules.tray.TrayController(
        desktop, icon_factory=lambda _: FakeIcon(fail=True), platform="win32"
    )
    assert not tray.start()
    tray.start_worker()
    try:
        assert started.wait(5)
    finally:
        tray.shutdown()
    assert desktop.test_state["actions"] == [("start", "hub", str(tmp_path))]


def test_tray_setup_visibility_failure_recovers_without_threads(modules, desktop):
    class BadIcon(FakeIcon):
        def run(self, setup):
            raise OSError("icon construction failed")

    tray = modules.tray.TrayController(
        desktop, icon_factory=lambda _: BadIcon(), platform="win32"
    )
    assert not tray.start()
    assert not tray._loop.is_alive()
    assert not tray.available


def test_invalid_saved_context_fails_once_without_retry(desktop, tmp_path):
    from chat2local.management.runtime import ManagementError

    desktop.preferences.current = replace(
        desktop.preferences.current,
        auto_start_core=True,
        startup_mode="hub",
        startup_workspace=str(tmp_path),
    )
    action = Mock(side_effect=ManagementError("Workspace is no longer allowed"))
    desktop.bridge._adapter.runtime.action = action
    desktop.auto_start_core()
    assert action.call_count == 1
    assert "no longer allowed" in desktop.error


def test_unconfirmed_starting_core_does_not_update_context(desktop, tmp_path):
    desktop.test_state["snapshot"] = running(tmp_path)
    desktop.test_state["snapshot"]["core"]["state"] = "starting"
    desktop.bridge.runtime_status()
    assert desktop.preferences.current.startup_mode is None


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.skipif(sys.platform != "win32", reason="Windows backend integration")
def test_pystray_setup_shim_leaves_no_helper_even_on_early_failure(
    modules, desktop, monkeypatch, fail
):
    pystray = pytest.importorskip("pystray")
    pytest.importorskip("PIL")
    from pystray._base import Icon

    class Backend(Icon):
        def _run(self):
            if fail:
                raise RuntimeError("before native ready")
            self._mark_ready()
            stopped.wait()

        def _stop(self):
            stopped.set()

        def _update_menu(self):
            pass

        def _update_icon(self):
            pass

        def _show(self):
            pass

        def _hide(self):
            pass

    stopped = threading.Event()
    monkeypatch.setattr(pystray, "Icon", Backend)
    tray = modules.tray.TrayController(desktop, platform="win32")
    assert tray.start() is (not fail)
    tray.shutdown()
    assert not tray._loop.is_alive()
    assert tray.icon._setup_thread is tray._loop
    assert not tray.icon._setup_thread.is_alive()
    assert next(iter(tray.icon.menu.items)).default


@pytest.mark.skipif(sys.platform != "win32", reason="Windows backend integration")
def test_false_native_tray_add_does_not_count_as_available(
    modules, desktop, monkeypatch
):
    pystray = pytest.importorskip("pystray")
    pytest.importorskip("PIL")
    from pystray._base import Icon
    from pystray._util import win32

    class NativeNotify:
        errcheck = None

        def __call__(self, *args):
            return self.errcheck(False, self, args)

    class Backend(Icon):
        def _run(self):
            self._mark_ready()

        def _stop(self):
            pass

        def _update_menu(self):
            pass

        def _update_icon(self):
            pass

        def _show(self):
            win32.Shell_NotifyIcon(None, None)

    notify = NativeNotify()
    monkeypatch.setattr(win32, "Shell_NotifyIcon", notify)
    monkeypatch.setattr(pystray, "Icon", Backend)
    tray = modules.tray.TrayController(desktop, platform="win32")
    assert not tray.start()
    assert not tray.available and not tray._loop.is_alive()
    assert notify.errcheck is None


def test_non_windows_tray_never_imports_native_backend(modules, desktop):
    factory = Mock(side_effect=AssertionError("native backend must not load"))
    tray = modules.tray.TrayController(desktop, icon_factory=factory, platform="linux")
    assert not tray.start()
    factory.assert_not_called()
    tray.shutdown()


def test_shutdown_prevents_new_core_operations(modules, desktop):
    operation = Mock()
    desktop.exiting = True
    modules.tray.TrayController(desktop)._operate(operation)
    operation.assert_not_called()
