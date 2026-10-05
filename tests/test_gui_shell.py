"""Shell loading modes without opening desktop windows or starting Core."""

from importlib import import_module
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def shell(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "gui/pywebview/python")
    )
    return import_module("app")


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
    desktop = SimpleNamespace(create_window=Mock(return_value=object()), start=Mock())
    monkeypatch.setitem(sys.modules, "webview", desktop)

    shell.main()

    configure.assert_not_called()
    desktop.create_window.assert_called_once()
    desktop.start.assert_called_once()
    assert not isolated_user_data.exists()
