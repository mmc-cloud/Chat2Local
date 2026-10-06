"""Source/frozen resource locations for the replaceable desktop shell."""

from __future__ import annotations

import sys
from pathlib import Path


def pywebview_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "gui" / "pywebview"
    return Path(__file__).resolve().parents[1]


PYWEBVIEW_ROOT = pywebview_root()
FRONTEND = PYWEBVIEW_ROOT / "frontend"
ASSETS = PYWEBVIEW_ROOT / "assets"
