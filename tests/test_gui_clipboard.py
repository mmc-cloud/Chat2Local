"""Exercise native clipboard ABI and ownership using fakes, never the user's clipboard."""
import ctypes
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from chat2local.management.runtime import ManagementError


@pytest.mark.parametrize("fail", [None, "OpenClipboard", "GlobalAlloc", "GlobalLock", "SetClipboardData"])
def test_clipboard_owner_encoding_and_cleanup(monkeypatch, fail):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "gui/pywebview/python"))
    module = import_module("clipboard")
    monkeypatch.setattr(module.sys, "platform", "win32")
    calls, payloads = [], []
    class Function:
        def __init__(self, name): self.name = name
        def __call__(self, *args):
            calls.append((self.name, args))
            return 0 if self.name == fail else 123
    user = SimpleNamespace(**{key: Function(key) for key in ("OpenClipboard", "EmptyClipboard", "SetClipboardData", "CloseClipboard")})
    kernel = SimpleNamespace(**{key: Function(key) for key in ("GlobalAlloc", "GlobalLock", "GlobalUnlock", "GlobalFree")})
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs: user if name == "user32" else kernel, raising=False)
    monkeypatch.setattr(ctypes, "memmove", lambda pointer, data, size: payloads.append(data))
    window = SimpleNamespace(native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 456)))
    if fail:
        with pytest.raises(ManagementError, match="Clipboard unavailable"):
            module.copy_text("test-copy-中文", window)
    else:
        module.copy_text("test-copy-中文", window)
        assert payloads == [("test-copy-中文\0").encode("utf-16-le")]
        assert not any(name == "GlobalFree" for name, _ in calls)
    assert calls[0] == ("OpenClipboard", (456,))
    if fail != "OpenClipboard":
        assert calls[-1][0] == "CloseClipboard"
    if fail in ("GlobalLock", "SetClipboardData"):
        assert any(name == "GlobalFree" for name, _ in calls)
