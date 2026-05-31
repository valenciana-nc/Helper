import ctypes
import io
import os
from ctypes import wintypes
from dataclasses import dataclass

import mss
from PIL import Image

from config import SCREENSHOT_MAX_EDGE

_LAST_FOREGROUND_MONITOR: dict[str, int] | None = None


def set_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


@dataclass(frozen=True)
class Capture:
    png_bytes: bytes
    width: int
    height: int
    monitor_left: int
    monitor_top: int
    scale: float

    def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
        sx = int(x / self.scale) + self.monitor_left
        sy = int(y / self.scale) + self.monitor_top
        return sx, sy


def _downscale(img: Image.Image, max_edge: int) -> tuple[Image.Image, float]:
    long_edge = max(img.width, img.height)
    if long_edge <= max_edge:
        return img, 1.0
    scale = max_edge / long_edge
    new_size = (int(img.width * scale), int(img.height * scale))
    return img.resize(new_size, Image.LANCZOS), scale


def _capture_monitor(monitor: dict[str, int], max_edge: int) -> Capture:
    with mss.mss() as sct:
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.rgb)
    scaled, ratio = _downscale(img, max_edge)
    buf = io.BytesIO()
    scaled.save(buf, format="PNG", optimize=True)
    return Capture(
        png_bytes=buf.getvalue(),
        width=scaled.width,
        height=scaled.height,
        monitor_left=monitor["left"],
        monitor_top=monitor["top"],
        scale=ratio,
    )


def capture_virtual_desktop(max_edge: int = SCREENSHOT_MAX_EDGE) -> Capture:
    with mss.mss() as sct:
        monitor = dict(sct.monitors[0])
    return _capture_monitor(monitor, max_edge)


def capture_primary(max_edge: int = SCREENSHOT_MAX_EDGE) -> Capture:
    with mss.mss() as sct:
        monitor = dict(sct.monitors[1])
    return _capture_monitor(monitor, max_edge)


def capture_active_monitor(max_edge: int = SCREENSHOT_MAX_EDGE) -> Capture:
    """Capture only the monitor containing the foreground window.

    Falls back to the primary display if the foreground window can't be
    resolved. Used by Help mode so the vision model spends its pixel budget
    on the screen the user is actually working on, not on the whole virtual
    desktop (which on multi-monitor setups wastes resolution on inactive
    screens and dead bounding-box space).
    """
    monitor = _active_monitor_dict()
    if monitor is None:
        return capture_primary(max_edge)
    return _capture_monitor(monitor, max_edge)


def _active_monitor_dict() -> dict[str, int] | None:
    global _LAST_FOREGROUND_MONITOR
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return _LAST_FOREGROUND_MONITOR
    if _is_own_process_window(hwnd):
        return _LAST_FOREGROUND_MONITOR or _cursor_monitor_dict()
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return _LAST_FOREGROUND_MONITOR or _cursor_monitor_dict()
    cx = (rect.left + rect.right) // 2
    cy = (rect.top + rect.bottom) // 2
    monitor = _monitor_containing_point(cx, cy)
    if monitor is not None:
        _LAST_FOREGROUND_MONITOR = monitor
    return monitor or _LAST_FOREGROUND_MONITOR or _cursor_monitor_dict()


def _is_own_process_window(hwnd: int) -> bool:
    pid = wintypes.DWORD()
    try:
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    except Exception:
        return False
    return int(pid.value) == os.getpid()


def _cursor_monitor_dict() -> dict[str, int] | None:
    point = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return None
    return _monitor_containing_point(int(point.x), int(point.y))


def _monitor_containing_point(x: int, y: int) -> dict[str, int] | None:
    with mss.mss() as sct:
        for monitor in sct.monitors[1:]:
            left = monitor["left"]
            top = monitor["top"]
            right = left + monitor["width"]
            bottom = top + monitor["height"]
            if left <= x < right and top <= y < bottom:
                return dict(monitor)
    return None


if __name__ == "__main__":
    from pathlib import Path

    set_dpi_aware()
    cap = capture_primary()
    out = Path(__file__).parent / "logs" / "screen_test.png"
    out.write_bytes(cap.png_bytes)
    print(f"Saved {out}")
    print(f"Captured {cap.width}x{cap.height} at scale {cap.scale:.3f}")
    print(f"Monitor origin: ({cap.monitor_left}, {cap.monitor_top})")
    cx, cy = cap.to_screen_coords(cap.width // 2, cap.height // 2)
    print(f"Center of image -> screen coords ({cx}, {cy})")
