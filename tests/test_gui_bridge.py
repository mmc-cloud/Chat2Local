"""GUI handshake stays independent from Core business modules."""

from importlib import import_module, metadata
from pathlib import Path

import pytest


@pytest.fixture
def bridge_module(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "gui/pywebview/python")
    )
    return import_module("bridge")


def test_ping_uses_installed_package_metadata(bridge_module):
    assert bridge_module.GuiBridge().ping() == {
        "ok": True,
        "app": "Chat2Local",
        "version": metadata.version("chat2local"),
        "backend": "pywebview",
    }


def test_ping_without_installed_core_is_explicit(bridge_module, monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(bridge_module, "version", missing)
    assert (
        bridge_module.GuiBridge().ping()["version"] == "unknown (package not installed)"
    )


def test_bridge_exposes_only_transport_methods(bridge_module):
    assert [
        name for name in dir(bridge_module.GuiBridge) if not name.startswith("_")
    ] == sorted(
        [
            "ping",
            "runtime_status",
            "start_core",
            "stop_core",
            "restart_core",
            "choose_workspace",
            "read_config",
            "validate_config",
            "save_config",
            "read_logs",
            "list_devices",
            "read_desktop_preferences",
            "save_desktop_preferences",
            "take_desktop_navigation",
        ]
    )


def test_bridge_returns_safe_errors(bridge_module):
    from types import SimpleNamespace

    from chat2local.runtime.config import ConfigError

    def invalid(*args):
        raise ConfigError("Invalid config: read.max_lines: must be positive")

    bridge = bridge_module.GuiBridge(SimpleNamespace(validate_config=invalid))
    result = bridge.validate_config({"read": {"max_lines": 0}})
    assert not result["ok"] and "read.max_lines" in result["error"]["message"]

    def unknown():
        raise RuntimeError("PRIVATE-PASSWORD")

    bridge._adapter = SimpleNamespace(read_config=unknown)
    result = bridge.read_config()
    assert result == {
        "ok": False,
        "error": {"message": "Internal GUI/Core error; see logs"},
    }
    assert "PRIVATE-PASSWORD" not in str(result)

    def invalid_value():
        raise ValueError("PRIVATE-PASSWORD")

    bridge._adapter = SimpleNamespace(read_config=invalid_value)
    assert bridge.read_config() == result


def test_folder_picker_cancellation_and_selection(bridge_module, monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules, "webview", SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=1))
    )
    bridge = bridge_module.GuiBridge()
    bridge._window = SimpleNamespace(create_file_dialog=lambda kind: None)
    assert bridge.choose_workspace() == {"ok": True, "result": None}
    bridge._window = SimpleNamespace(create_file_dialog=lambda kind: ("D:/space path",))
    assert bridge.choose_workspace()["result"] == "D:/space path"
