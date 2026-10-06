"""Shell loading modes without opening desktop windows or starting Core."""

import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest


@pytest.fixture
def shell(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "gui/pywebview/python")
    )
    app = import_module("app")
    # All shell tests stay isolated from real session ownership / production IPC.
    instance = Mock()
    instance.claim.return_value = True
    monkeypatch.setattr(app, "SingleInstance", Mock(return_value=instance))
    # Primary boot now reconciles Run state; never reach real HKCU in shell tests.
    monkeypatch.setattr(
        app, "WindowsAutostart", Mock(return_value=Mock(supported=True))
    )
    return app


def test_build_loads_file_uri_without_server(shell, tmp_path, monkeypatch):
    frontend = tmp_path / "frontend with spaces"
    (frontend / "dist").mkdir(parents=True)
    (frontend / "dist/index.html").write_text("<html></html>")
    monkeypatch.setattr(shell, "FRONTEND", frontend)
    assert shell.frontend_url(None) == (frontend / "dist/index.html").as_uri()


def test_missing_build_has_actionable_error(shell, tmp_path, monkeypatch):
    monkeypatch.setattr(shell, "FRONTEND", tmp_path)
    with pytest.raises(FileNotFoundError, match="pnpm build"):
        shell.frontend_url(None)


def test_custom_dev_url(shell):
    assert shell.frontend_url("http://127.0.0.1:5273") == "http://127.0.0.1:5273"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5173",
        "http://127.0.0.2:5173",
        "https://[::1]:5173",
    ],
)
def test_loopback_dev_urls(shell, url):
    assert shell.frontend_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/example.html",
        "javascript:alert(1)",
        "http://",
        "https://example.com",
        "http://192.168.1.20:5173",
        "http://user:secret@127.0.0.1:5173",
        "http://127.0.0.1:5173/?token=secret",
        "http://127.0.0.1:5173/#fragment",
        "http://127.0.0.1:0",
    ],
)
def test_invalid_dev_url(shell, url):
    with pytest.raises(ValueError, match="loopback http"):
        shell.frontend_url(url)


@pytest.mark.parametrize("dev_url", [None, "http://127.0.0.1:5173"])
def test_shell_start_never_configures_core_logging(
    shell, tmp_path, monkeypatch, isolated_user_data, dev_url
):
    from chat2local.runtime import logging as core_logging

    frontend = tmp_path / "frontend"
    (frontend / "dist").mkdir(parents=True)
    (frontend / "dist/index.html").write_text("<html></html>")
    monkeypatch.setattr(shell, "FRONTEND", frontend)
    monkeypatch.setattr(
        sys, "argv", ["app.py", *(["--dev-url", dev_url] if dev_url else [])]
    )
    configure = Mock(side_effect=AssertionError("GUI must not configure Core logging"))
    monkeypatch.setattr(core_logging, "configure_logging", configure)

    class Event:
        def __iadd__(self, handler):
            return self

    window = SimpleNamespace(
        events=SimpleNamespace(before_show=Event(), closing=Event())
    )
    desktop = SimpleNamespace(create_window=Mock(return_value=window), start=Mock())
    monkeypatch.setitem(sys.modules, "webview", desktop)
    monkeypatch.setattr(
        shell,
        "TrayController",
        lambda _: SimpleNamespace(
            available=False,
            start=Mock(),
            start_worker=Mock(),
            shutdown=Mock(),
        ),
    )

    shell.main()

    configure.assert_not_called()
    desktop.create_window.assert_called_once()
    desktop.start.assert_called_once()
    assert desktop.start.call_args.kwargs["private_mode"] is False
    assert desktop.start.call_args.kwargs["storage_path"] == str(
        isolated_user_data / "gui" / "webview"
    )
    assert (isolated_user_data / "gui/desktop.log").is_file()
    assert not (isolated_user_data / "logs/chat2local.log").exists()
    shell.WindowsAutostart.return_value.reconcile.assert_called_once_with(False)
    assert not shell.logging.getLogger("chat2local.desktop").handlers


@pytest.mark.parametrize(
    "startup,silent,available,hidden",
    [
        (False, True, True, False),
        (True, False, True, False),
        (True, True, True, True),
        (True, True, False, False),
    ],
)
def test_native_window_created_with_correct_visibility(
    shell, tmp_path, monkeypatch, startup, silent, available, hidden
):
    from dataclasses import replace

    from preferences import PreferencesStore

    preferences = PreferencesStore()
    preferences.save(replace(preferences.current, silent_login_start=silent))
    frontend = tmp_path / "frontend"
    (frontend / "dist").mkdir(parents=True)
    (frontend / "dist/index.html").write_text("<html></html>")
    monkeypatch.setattr(shell, "FRONTEND", frontend)
    monkeypatch.setattr(sys, "argv", ["app.py", *(["--startup"] if startup else [])])

    class Event:
        def __iadd__(self, handler):
            return self

    window = SimpleNamespace(
        events=SimpleNamespace(before_show=Event(), closing=Event())
    )
    webview = SimpleNamespace(create_window=Mock(return_value=window), start=Mock())
    tray = SimpleNamespace(
        available=available, start=Mock(), start_worker=Mock(), shutdown=Mock()
    )
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(shell, "TrayController", lambda _: tray)
    shell.main()
    assert webview.create_window.call_args.kwargs["hidden"] is hidden
    webview.create_window.assert_called_once()
    tray.shutdown.assert_called_once()


def test_native_window_creation_failure_cleans_tray_and_logging(
    shell, tmp_path, monkeypatch
):
    frontend = tmp_path / "frontend"
    (frontend / "dist").mkdir(parents=True)
    (frontend / "dist/index.html").write_text("<html></html>")
    monkeypatch.setattr(shell, "FRONTEND", frontend)
    monkeypatch.setattr(sys, "argv", ["app.py"])
    tray = SimpleNamespace(available=True, start=Mock(), shutdown=Mock())
    monkeypatch.setattr(shell, "TrayController", lambda _: tray)
    monkeypatch.setitem(
        sys.modules,
        "webview",
        SimpleNamespace(create_window=Mock(side_effect=RuntimeError("window failed"))),
    )
    with pytest.raises(RuntimeError, match="window failed"):
        shell.main()
    tray.shutdown.assert_called_once()
    assert not shell.logging.getLogger("chat2local.desktop").handlers


@pytest.mark.parametrize("startup", [False, True])
def test_secondary_exits_before_desktop_tray_webview_or_core(
    shell, monkeypatch, startup, isolated_user_data
):
    instance = shell.SingleInstance.return_value
    instance.claim.return_value = False
    desktop = Mock(side_effect=AssertionError("Secondary must not create DesktopShell"))
    frontend = Mock(side_effect=AssertionError("Secondary does not load frontend"))
    webview = SimpleNamespace(create_window=Mock(), start=Mock())
    monkeypatch.setattr(shell, "DesktopShell", desktop)
    monkeypatch.setattr(shell, "frontend_url", frontend)
    logging_context = Mock(wraps=shell.desktop_logging)
    monkeypatch.setattr(shell, "desktop_logging", logging_context)
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(sys, "argv", ["app.py", *(["--startup"] if startup else [])])
    assert shell.main() is None
    instance.claim.assert_called_once_with(startup=startup)
    instance.start.assert_not_called()
    instance.close.assert_called_once()
    desktop.assert_not_called()
    webview.create_window.assert_not_called()
    webview.start.assert_not_called()
    logging_context.assert_not_called()
    shell.WindowsAutostart.assert_not_called()
    assert not (isolated_user_data / "gui/desktop.log").exists()
    assert not shell.logging.getLogger("chat2local.desktop").handlers


def test_ownership_failure_never_creates_desktop(
    shell, monkeypatch, isolated_user_data, capsys
):
    instance = shell.SingleInstance.return_value
    instance.claim.side_effect = OSError(
        "https://user:SUPER-SECRET@example.com TOKEN-123456"
    )
    desktop = Mock()
    monkeypatch.setattr(shell, "DesktopShell", desktop)
    monkeypatch.setattr(sys, "argv", ["app.py"])
    with pytest.raises(SystemExit) as exit_error:
        shell.main()
    assert exit_error.value.code == 1
    desktop.assert_not_called()
    instance.close.assert_called_once()
    shell.WindowsAutostart.assert_not_called()
    assert not (isolated_user_data / "gui/desktop.log").exists()
    assert not shell.logging.getLogger("chat2local.desktop").handlers
    stderr = capsys.readouterr().err
    assert "Desktop ownership/activation failed" in stderr and "OSError" in stderr
    assert "SUPER-SECRET" not in stderr and "TOKEN-123456" not in stderr


def test_desktop_shutdown_failure_still_closes_instance(shell, monkeypatch):
    desktop = Mock()
    desktop.tray.available = True
    desktop.shutdown.side_effect = RuntimeError("shutdown failed")
    monkeypatch.setattr(shell, "DesktopShell", Mock(return_value=desktop))
    monkeypatch.setattr(shell, "frontend_url", Mock(return_value="file:///test.html"))
    monkeypatch.setitem(sys.modules, "webview", Mock())
    monkeypatch.setattr(sys, "argv", ["app.py"])
    with pytest.raises(RuntimeError, match="shutdown failed"):
        shell.main()
    shell.SingleInstance.return_value.close.assert_called_once()


def test_primary_closes_file_handler_before_releasing_ownership(
    shell, monkeypatch, isolated_user_data
):
    desktop = Mock()
    monkeypatch.setattr(shell, "DesktopShell", Mock(return_value=desktop))
    monkeypatch.setattr(shell, "frontend_url", Mock(return_value="file:///test.html"))
    monkeypatch.setitem(sys.modules, "webview", MagicMock())
    monkeypatch.setattr(sys, "argv", ["app.py"])
    handlers = []
    original_handler = shell.RotatingFileHandler

    def file_handler(*args, **kwargs):
        assert shell.SingleInstance.return_value.claim.call_count == 1
        handler = original_handler(*args, **kwargs)
        handlers.append(handler)
        return handler

    def release():
        assert handlers and all(handler.stream is None for handler in handlers)
        assert not any(
            isinstance(handler, original_handler)
            for handler in shell.logging.getLogger("chat2local.desktop").handlers
        )

    monkeypatch.setattr(shell, "RotatingFileHandler", file_handler)
    shell.SingleInstance.return_value.close.side_effect = release
    shell.main()
    assert (isolated_user_data / "gui/desktop.log").is_file()
    desktop.reconcile_login_startup.assert_called_once()


def test_primary_startup_order_reconciles_before_listener_tray_and_webview(
    shell, monkeypatch
):
    steps = []
    instance = shell.SingleInstance.return_value
    instance.claim.side_effect = lambda **_: steps.append("claim") or True
    instance.start.side_effect = lambda _: steps.append("listener")
    desktop = Mock()
    desktop.reconcile_login_startup.side_effect = lambda: steps.append("reconcile")
    desktop.tray.start.side_effect = lambda: steps.append("tray")
    desktop.shutdown.side_effect = lambda: steps.append("shutdown")
    instance.close.side_effect = lambda: steps.append("release")

    def create_desktop(**kwargs):
        steps.append("preferences")
        return desktop

    from contextlib import contextmanager

    original_logging = shell.desktop_logging

    @contextmanager
    def enter_logging():
        steps.append("logging")
        with original_logging():
            yield
        steps.append("log-closed")

    def create_window(*args, **kwargs):
        steps.append("window")
        return MagicMock()

    monkeypatch.setattr(shell, "DesktopShell", create_desktop)
    monkeypatch.setattr(shell, "desktop_logging", enter_logging)
    monkeypatch.setattr(shell, "frontend_url", Mock(return_value="file:///test.html"))
    monkeypatch.setitem(
        sys.modules,
        "webview",
        SimpleNamespace(
            create_window=create_window,
            start=lambda *args, **kwargs: steps.append("webview"),
        ),
    )
    monkeypatch.setattr(sys, "argv", ["app.py"])
    shell.main()
    assert steps == [
        "claim",
        "logging",
        "preferences",
        "reconcile",
        "listener",
        "tray",
        "window",
        "webview",
        "shutdown",
        "log-closed",
        "release",
    ]


def test_reconcile_failure_keeps_gui_starting_and_preferences_intent(
    shell, monkeypatch, isolated_user_data
):
    from dataclasses import replace

    from preferences import PreferencesStore

    store = PreferencesStore()
    store.save(replace(store.current, launch_at_login=True))
    previous = store.path.read_bytes()
    shell.WindowsAutostart.return_value.reconcile.side_effect = OSError(
        "https://user:SUPER-SECRET@example.com TOKEN-123456"
    )
    desktop = shell.DesktopShell(preferences=store)
    desktop.bridge._adapter.runtime_status = Mock(
        return_value={"lifecycle": "Stopped", "core": None}
    )
    desktop.tray = Mock(available=True)
    monkeypatch.setattr(shell, "DesktopShell", Mock(return_value=desktop))
    monkeypatch.setattr(shell, "frontend_url", Mock(return_value="file:///test.html"))
    webview = MagicMock()
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(sys, "argv", ["app.py"])
    shell.main()
    webview.create_window.assert_called_once()
    webview.start.assert_called_once()
    desktop.tray.start.assert_called_once()
    shell.SingleInstance.return_value.start.assert_called_once()
    shell.WindowsAutostart.return_value.reconcile.assert_called_once_with(True)
    assert store.current.launch_at_login and store.path.read_bytes() == previous
    assert (
        desktop.bridge.runtime_status()["result"]["desktop_error"]
        == "Could not synchronize Windows login startup; see desktop log"
    )
    contents = (isolated_user_data / "gui/desktop.log").read_text(encoding="utf-8")
    assert (
        "Could not synchronize Windows login startup" in contents
        and "OSError" in contents
    )
    assert "SUPER-SECRET" not in contents and "TOKEN-123456" not in contents


def test_claim_failure_without_console_is_safe_and_leaves_no_file_handler(
    shell, monkeypatch, isolated_user_data
):
    bootstrap = import_module("desktop_logging")
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(stderr=None))
    shell.SingleInstance.return_value.claim.side_effect = RuntimeError("TOKEN-123456")
    monkeypatch.setattr(sys, "argv", ["app.py"])
    with pytest.raises(SystemExit) as exit_error:
        shell.main()
    assert exit_error.value.code == 1
    assert not shell.logging.getLogger("chat2local.desktop").handlers
    assert not (isolated_user_data / "gui/desktop.log").exists()
