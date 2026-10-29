"""Test-owned subprocesses remain windowless when pytest runs in the background."""

import json
import subprocess
import sys

from conftest import HIDDEN_SUBPROCESS_OPTIONS


def test_child_from_consoleless_parent_has_no_window():
    child_script = """
import ctypes, json, sys
has_window = None
if sys.platform == 'win32':
    kernel = ctypes.WinDLL('kernel32')
    kernel.GetConsoleWindow.restype = ctypes.c_void_p
    has_window = bool(kernel.GetConsoleWindow())
print(json.dumps({'has_window': has_window}))
"""
    launcher_script = """
import ctypes, json, subprocess, sys
if sys.platform == 'win32':
    # An invisible inherited console could otherwise mask missing child flags.
    ctypes.WinDLL('kernel32').FreeConsole()
child = subprocess.run(
    [sys.executable, '-c', sys.argv[1]], capture_output=True, text=True,
    check=True, timeout=10, **json.loads(sys.argv[2]),
)
sys.stdout.write(child.stdout)
"""
    result = subprocess.run(
        [sys.executable, "-c", launcher_script, child_script, json.dumps(HIDDEN_SUBPROCESS_OPTIONS)],
        capture_output=True, text=True, check=True, timeout=20,
        **HIDDEN_SUBPROCESS_OPTIONS,
    )

    expected = False if sys.platform == "win32" else None
    assert json.loads(result.stdout) == {"has_window": expected}
