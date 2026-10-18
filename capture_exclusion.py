"""Keep Helper's teaching overlays out of the screenshots used for feedback."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


def exclude_from_capture(widget) -> bool:
    if sys.platform != "win32":
        return False
    try:
        setter = ctypes.windll.user32.SetWindowDisplayAffinity
        setter.argtypes = [wintypes.HWND, wintypes.DWORD]
        setter.restype = wintypes.BOOL
        # WDA_EXCLUDEFROMCAPTURE, supported on Windows 10 2004 and later.
        return bool(setter(int(widget.winId()), 0x11))
    except Exception:
        return False
