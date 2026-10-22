"""Native Windows clipboard transport; secret text never crosses the JS bridge."""
import sys

from chat2local.management.runtime import ManagementError


def copy_text(text: str, window=None) -> None:
    if sys.platform != "win32":
        raise ManagementError("Native token copy is only available on Windows")
    import ctypes
    from ctypes import wintypes

    try:
        owner = int(window.native.Handle.ToInt64())
        if not owner:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise ManagementError("Desktop window unavailable") from None

    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    if not user.OpenClipboard(owner):
        raise ManagementError("Clipboard unavailable; try copying again")
    handle = None
    try:
        data = (text + "\0").encode("utf-16-le")
        handle = kernel.GlobalAlloc(0x0002, len(data))
        pointer = kernel.GlobalLock(handle) if handle else None
        if not pointer:
            raise OSError
        ctypes.memmove(pointer, data, len(data))
        kernel.GlobalUnlock(handle)
        if not user.EmptyClipboard() or not user.SetClipboardData(13, handle):
            raise OSError
        handle = None  # Windows now owns the allocation.
    except OSError:
        raise ManagementError("Clipboard unavailable; try copying again") from None
    finally:
        if handle:
            kernel.GlobalFree(handle)
        user.CloseClipboard()
