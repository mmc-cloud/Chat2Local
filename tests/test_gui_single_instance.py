"""Isolated desktop IPC and mocked Windows ownership; no real GUI or Core."""

import json
import socket
import subprocess
import sys
import threading
import time
import uuid
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "gui/pywebview/python")
    )
    return import_module("single_instance")


def owner(primary=True, session=7):
    return SimpleNamespace(
        session_id=session, acquire=Mock(return_value=primary), close=Mock()
    )


@pytest.fixture
def primary(module, tmp_path):
    instance = module.SingleInstance(
        tmp_path / "gui", ownership=owner(), platform="win32"
    )
    assert instance.claim()
    try:
        yield instance
    finally:
        instance.close()


def secondary(module, primary, **options):
    return module.SingleInstance(
        primary.directory, ownership=owner(False), platform="win32", **options
    )


def test_first_instance_is_primary_and_shutdown_releases_resources(primary):
    activate = Mock()
    primary.start(activate)
    descriptor = json.loads(primary.path.read_text())
    assert set(descriptor) == {"schema_version", "pid", "session_id", "port", "nonce"}
    assert descriptor["session_id"] == 7
    assert primary._listener.getsockname()[0] == "127.0.0.1"
    listener, thread = primary._listener, primary._thread
    primary.close()
    assert not thread.is_alive()
    assert listener.fileno() == -1
    assert not primary.path.exists()
    assert not primary.primary
    primary.ownership.close.assert_called_once()
    activate.assert_not_called()


def test_manual_duplicate_activates_and_never_listens(module, primary):
    activated = threading.Event()
    primary.start(lambda: activated.set() or True)
    duplicate = secondary(module, primary)
    assert duplicate.claim() is False
    assert activated.wait(1)
    assert duplicate._listener is None and duplicate._thread is None
    duplicate.close()
    duplicate.ownership.close.assert_called_once()


def test_startup_duplicate_does_not_activate_or_read_descriptor(
    module, primary, monkeypatch
):
    activated = Mock()
    primary.start(activated)
    duplicate = secondary(module, primary)
    request = Mock(side_effect=AssertionError("Startup duplicate must stay silent"))
    monkeypatch.setattr(duplicate, "activate_existing", request)
    assert duplicate.claim(startup=True) is False
    request.assert_not_called()
    activated.assert_not_called()


def test_stale_descriptor_free_mutex_is_replaced(primary):
    primary.directory.mkdir()
    primary.path.write_text('{"stale":true}')
    primary.start(Mock())
    assert json.loads(primary.path.read_text())["nonce"] == primary._nonce
    assert not list(primary.directory.glob(".instance-*.tmp"))


def test_simultaneous_start_waits_for_activation_readiness(module, primary):
    duplicate = secondary(module, primary, retry_timeout=1)
    activated = threading.Event()
    # Fake ownership lets us delay publication without touching real Win32 state.
    ready = threading.Timer(0.1, primary.start, args=(lambda: activated.set() or True,))
    ready.start()
    try:
        assert duplicate.claim() is False
        assert activated.wait(1)
    finally:
        ready.join(timeout=1)
    assert not ready.is_alive()


def test_unready_owner_has_bounded_retry_and_never_becomes_primary(
    module, primary, caplog
):
    duplicate = secondary(module, primary, retry_timeout=0.12)
    started = time.monotonic()
    assert duplicate.claim() is False
    assert 0.1 <= time.monotonic() - started < 1
    assert not duplicate.primary and duplicate._listener is None
    assert "activation was unavailable" in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "activate", "nonce": "invalid"},
        {"action": "other", "nonce": "valid"},
        {"action": "activate", "nonce": "valid", "command": "ignored"},
        {"action": "activate", "nonce": "\u2603"},
    ],
)
def test_invalid_nonce_or_protocol_is_rejected(primary, payload):
    activate = Mock()
    primary.start(activate)
    if payload["nonce"] == "valid":
        payload["nonce"] = primary._nonce
    with socket.create_connection(
        primary._listener.getsockname(), timeout=1
    ) as connection:
        connection.sendall(json.dumps(payload).encode() + b"\n")
        response = connection.recv(32)
    assert response != b"ok\n"
    activate.assert_not_called()


def test_activation_restores_existing_window_outside_socket_thread(module, primary):
    app = import_module("app")
    desktop = app.DesktopShell()
    desktop.window = Mock()
    desktop.tray = Mock()
    primary.start(desktop.request_activation)
    assert secondary(module, primary).claim() is False
    desktop.window.show.assert_not_called()
    desktop.activate_pending()
    assert desktop.window.method_calls == [call.show(), call.restore(), call.show()]
    desktop.activate_pending()
    assert desktop.window.show.call_count == 2
    desktop.exiting = True
    assert desktop.request_activation() is False
    assert not desktop._activation_requested.is_set()
    desktop.activate_pending()
    assert desktop.window.show.call_count == 2


def test_slow_client_cannot_keep_listener_alive_at_shutdown(primary):
    primary.start(Mock())
    with socket.create_connection(
        primary._listener.getsockname(), timeout=1
    ) as connection:
        connection.sendall(b'{"action":')
        primary.close()
    assert not primary._thread.is_alive()
    assert primary._listener.fileno() == -1


def test_failed_atomic_publication_closes_listener_and_ownership(
    primary, module, monkeypatch
):
    monkeypatch.setattr(module.os, "replace", Mock(side_effect=OSError("failed")))
    with pytest.raises(OSError):
        primary.start(Mock())
    assert not primary._thread.is_alive()
    assert primary._listener.fileno() == -1
    assert not list(primary.directory.glob(".instance-*.tmp"))
    assert not primary.path.exists()
    primary.ownership.close.assert_called_once()


def test_session_descriptors_do_not_clobber_each_other(module, primary):
    other = module.SingleInstance(
        primary.directory, ownership=owner(session=8), platform="win32"
    )
    try:
        assert other.claim()
        primary.start(Mock())
        other.start(Mock())
        assert primary.path != other.path
        assert primary.path.exists() and other.path.exists()
        primary.close()
        assert other.path.exists()
    finally:
        other.close()


def test_non_windows_never_loads_win32(module, monkeypatch, tmp_path):
    monkeypatch.setattr(
        module, "WindowsOwnership", Mock(side_effect=AssertionError("Not Windows"))
    )
    instance = module.SingleInstance(tmp_path, platform="linux")
    assert instance.claim()
    instance.start(Mock())
    instance.close()
    assert instance._listener is None


@pytest.mark.parametrize("result,expected", [(0, True), (0x80, True), (0x102, False)])
def test_win32_ownership_and_crash_abandonment_are_mocked(
    module, monkeypatch, result, expected
):
    def session_id(pid, output):
        output._obj.value = 7
        return True

    kernel = SimpleNamespace(
        ProcessIdToSessionId=Mock(side_effect=session_id),
        CreateMutexW=Mock(return_value=42),
        WaitForSingleObject=Mock(return_value=result),
        ReleaseMutex=Mock(return_value=True),
        CloseHandle=Mock(return_value=True),
    )
    monkeypatch.setattr(module, "_kernel32", lambda: kernel)
    ownership = module.WindowsOwnership()
    assert ownership.session_id == 7
    assert ownership.acquire() is expected
    ownership.close()
    ownership.close()
    kernel.CreateMutexW.assert_called_once_with(
        None, False, r"Local\Chat2Local.Desktop"
    )
    kernel.WaitForSingleObject.assert_called_once_with(42, 0)
    kernel.CloseHandle.assert_called_once_with(42)
    assert bool(kernel.ReleaseMutex.call_count) is expected


def test_child_process_activation_without_gui_registry_or_real_mutex(module, primary):
    activated = threading.Event()
    primary.start(lambda: activated.set() or True)
    script = """
import sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from single_instance import SingleInstance
from pathlib import Path
ownership = SimpleNamespace(session_id=7, acquire=lambda: False, close=lambda: None)
instance = SingleInstance(Path(sys.argv[2]), ownership=ownership, platform='win32')
assert instance.claim() is False
instance.close()
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(Path(module.__file__).parent),
            str(primary.directory),
        ],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert activated.wait(1)


def test_activation_listener_unknown_error_log_omits_nonce_and_secrets(
    primary, module, isolated_user_data
):
    app = import_module("app")

    def fail():
        raise RuntimeError("https://user:SUPER-SECRET@example.com TOKEN-123456")

    with app.desktop_logging():
        primary.start(fail)
        assert secondary(module, primary, retry_timeout=0.1).claim() is False
        primary.close()
    contents = (isolated_user_data / "gui/desktop.log").read_text(encoding="utf-8")
    assert "SUPER-SECRET" not in contents and "TOKEN-123456" not in contents
    assert primary._nonce not in contents
    assert (
        "RuntimeError" in contents and "Desktop activation callback failed" in contents
    )


def test_descriptor_cleanup_error_still_releases_mutex(primary, monkeypatch):
    primary.start(Mock())
    monkeypatch.setattr(
        primary, "_read_descriptor", Mock(side_effect=OSError("denied"))
    )
    primary.close()
    primary.ownership.close.assert_called_once()
    assert not primary._thread.is_alive()


@pytest.mark.parametrize("failure", ["session", "create", "wait"])
def test_win32_ownership_failure_is_closed_and_never_bypassed(
    module, monkeypatch, failure
):
    kernel = SimpleNamespace(
        ProcessIdToSessionId=Mock(return_value=failure != "session"),
        CreateMutexW=Mock(return_value=0 if failure == "create" else 42),
        WaitForSingleObject=Mock(return_value=0xFFFFFFFF),
        ReleaseMutex=Mock(),
        CloseHandle=Mock(return_value=True),
    )
    monkeypatch.setattr(module, "_kernel32", lambda: kernel)
    with pytest.raises(OSError):
        module.WindowsOwnership().acquire()
    kernel.ReleaseMutex.assert_not_called()
    if failure == "wait":
        kernel.CloseHandle.assert_called_once_with(42)
    else:
        kernel.CloseHandle.assert_not_called()


def test_existing_desktop_worker_processes_activation(primary, module):
    app = import_module("app")
    tray = import_module("tray")
    desktop = app.DesktopShell()
    activated = threading.Event()
    callback_threads = []

    def show():
        callback_threads.append(threading.current_thread().name)
        activated.set()

    desktop.window = Mock(show=Mock(side_effect=show))
    desktop.auto_start_core = Mock()
    desktop.bridge.runtime_status = Mock(
        return_value={"ok": True, "result": {"lifecycle": "Stopped", "core": None}}
    )
    desktop.tray = tray.TrayController(desktop, platform="win32")
    primary.start(desktop.request_activation)
    desktop.tray.start_worker()
    try:
        assert secondary(module, primary).claim() is False
        assert activated.wait(2)
    finally:
        desktop.shutdown()
    assert callback_threads and set(callback_threads) == {"chat2local-desktop"}
    assert desktop.window.method_calls == [call.show(), call.restore(), call.show()]
    assert not desktop.tray._worker.is_alive()


def test_exiting_primary_rejects_activation_without_event_or_window(
    module, primary, caplog
):
    app = import_module("app")
    desktop = app.DesktopShell()
    desktop.window = Mock()
    desktop.exiting = True
    primary.start(desktop.request_activation)
    with socket.create_connection(
        primary._listener.getsockname(), timeout=1
    ) as connection:
        connection.sendall(
            json.dumps({"action": "activate", "nonce": primary._nonce}).encode() + b"\n"
        )
        assert connection.recv(32) == b"no\n"
    assert secondary(module, primary, retry_timeout=0.1).claim() is False
    assert "activation was unavailable" in caplog.text
    assert not desktop._activation_requested.is_set()
    desktop.activate_pending()
    desktop.window.show.assert_not_called()
    desktop.window.restore.assert_not_called()


@pytest.mark.parametrize(
    "accepted,response",
    [(True, b"ok\n"), (False, b"no\n"), (None, b"no\n"), (1, b"no\n")],
)
def test_listener_only_acknowledges_explicit_activation_acceptance(
    primary, accepted, response
):
    activate = Mock(return_value=accepted)
    primary.start(activate)
    with socket.create_connection(
        primary._listener.getsockname(), timeout=1
    ) as connection:
        connection.sendall(
            json.dumps({"action": "activate", "nonce": primary._nonce}).encode() + b"\n"
        )
        assert connection.recv(32) == response
    activate.assert_called_once()


@pytest.mark.skipif(
    sys.platform != "win32", reason="Real isolated Windows session mutex"
)
def test_real_windows_process_ownership_activation_single_writer_and_reacquire(
    module, tmp_path
):
    host = Path(__file__).parent / "fixtures/gui_desktop_smoke_host.py"
    directory = tmp_path / "isolated-desktop"
    directory.mkdir()
    mutex_name = rf"Local\Chat2Local.Desktop.Test.{uuid.uuid4().hex}"
    command = [
        sys.executable,
        str(host),
        str(Path(module.__file__).parent),
        str(directory),
        mutex_name,
    ]
    process = subprocess.Popen(
        [*command, "primary"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 8
        while not (directory / "ready.json").exists():
            assert process.poll() is None, process.communicate(timeout=1)
            assert time.monotonic() < deadline, "Isolated primary did not become ready"
            time.sleep(0.01)
        ready = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
        assert ready["primary"] is True and ready["file_handlers"] == 1
        assert (directory / "gui/desktop.log").is_file()
        assert len(list((directory / "gui").glob("instance-*.json"))) == 1

        for role in ("manual", "startup"):
            result = subprocess.run(
                [*command, role], capture_output=True, check=False, timeout=8
            )
            assert result.returncode == 0, result.stderr.decode(errors="replace")
            secondary_trace = json.loads(result.stdout)
            assert secondary_trace["primary"] is False
            assert secondary_trace["activation_succeeded"] is (role == "manual")
            if role == "manual":
                deadline = time.monotonic() + 2
                while not (directory / "activated").exists():
                    assert time.monotonic() < deadline, (
                        "Primary did not process activation"
                    )
                    time.sleep(0.01)
            for name in (
                "file_handlers",
                "shells",
                "reconciles",
                "tray_starts",
                "windows",
                "auto_start_calls",
            ):
                assert secondary_trace[name] == 0
        (directory / "stop").touch()
        output, errors = process.communicate(timeout=5)
        assert process.returncode == 0, errors.decode(errors="replace")
        primary_trace = json.loads(output)
        assert primary_trace["activations"] == 2
        assert primary_trace["file_closed_before_release"] is True
        assert not primary_trace["listener_alive_after_close"]
        assert not list((directory / "gui").glob("instance-*.json"))

        result = subprocess.run(
            [*command, "probe"], capture_output=True, check=False, timeout=8
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        new_trace = json.loads(result.stdout)
        assert new_trace["primary"] is True and new_trace["file_handlers"] == 1
        assert new_trace["file_closed_before_release"] is True
        assert not new_trace["listener_alive_after_close"]
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)
