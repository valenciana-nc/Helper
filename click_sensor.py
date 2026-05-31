from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable

HC_ACTION = 0
WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


MouseCallback = Callable[[int, int, bool], None]


class ClickSensor:
    """Low-level click classifier used by tests and optional native hooks."""

    def __init__(self, callback: MouseCallback) -> None:
        self._callback = callback
        self._target: tuple[int, int, int, int] | None = None
        self._hook = None
        self._proc = None

    def set_target(self, x: int, y: int, width: int, height: int) -> None:
        self._target = (int(x), int(y), int(width), int(height))

    def clear_target(self) -> None:
        self._target = None

    def start(self) -> None:
        if self._hook is not None:
            return
        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.LPARAM,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._proc = callback_type(self._low_level_proc)
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        if not self._hook:
            self._proc = None
            raise ctypes.WinError()

    def stop(self) -> None:
        if self._hook is None:
            return
        ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        self._proc = None

    def _low_level_proc(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == HC_ACTION and int(w_param) == WM_LBUTTONDOWN:
            target = self._target
            if target is not None:
                event = ctypes.cast(
                    l_param,
                    ctypes.POINTER(_MSLLHOOKSTRUCT),
                ).contents
                x = int(event.pt.x)
                y = int(event.pt.y)
                tx, ty, tw, th = target
                inside = tx <= x < tx + tw and ty <= y < ty + th
                self._callback(x, y, inside)
        try:
            return int(ctypes.windll.user32.CallNextHookEx(None, n_code, w_param, l_param))
        except Exception:
            return 0

