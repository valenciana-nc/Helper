"""Map physical Windows monitor coordinates to Qt screen-local coordinates."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PyQt6.QtCore import QPointF, QRect


user32 = ctypes.windll.user32 if sys.platform == "win32" else None


class _MonitorInfoEx(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def local_screen_point(
    x: float,
    y: float,
    screen_geometry: QRect,
    *,
    device_pixel_ratio: float = 1.0,
    native_screen_geometry: QRect | None = None,
) -> QPointF | None:
    native_screen = native_screen_geometry or _native_screen_geometry(screen_geometry, device_pixel_ratio)
    if not native_screen.contains(int(x), int(y)):
        return None
    dpr = _safe_device_pixel_ratio(device_pixel_ratio)
    return QPointF(
        (float(x) - native_screen.x()) / dpr,
        (float(y) - native_screen.y()) / dpr,
    )


def screen_device_pixel_ratio(screen) -> float:
    try:
        return _safe_device_pixel_ratio(float(screen.devicePixelRatio()))
    except Exception:
        return 1.0


def screen_native_geometry(screen) -> QRect:
    dpr = screen_device_pixel_ratio(screen)
    try:
        geometry = screen.geometry()
    except Exception:
        geometry = QRect()
    inferred = _native_screen_geometry(geometry, dpr)
    monitors = _native_monitor_geometries()
    if not monitors:
        return inferred

    try:
        screen_name = str(screen.name() or "")
    except Exception:
        screen_name = ""
    normalized_name = _normalized_monitor_name(screen_name)
    if normalized_name:
        for monitor_name, rect in monitors:
            if _normalized_monitor_name(monitor_name) == normalized_name:
                return rect

    inferred_center = inferred.center()
    for _monitor_name, rect in monitors:
        if rect.contains(inferred_center):
            return rect
    return inferred


def _safe_device_pixel_ratio(device_pixel_ratio: float) -> float:
    if device_pixel_ratio <= 0:
        return 1.0
    return max(0.25, min(8.0, float(device_pixel_ratio)))


def _native_screen_geometry(screen_geometry: QRect, device_pixel_ratio: float) -> QRect:
    dpr = _safe_device_pixel_ratio(device_pixel_ratio)
    return QRect(
        int(round(screen_geometry.x() * dpr)),
        int(round(screen_geometry.y() * dpr)),
        max(1, int(round(screen_geometry.width() * dpr))),
        max(1, int(round(screen_geometry.height() * dpr))),
    )


def _native_monitor_geometries() -> list[tuple[str, QRect]]:
    if sys.platform != "win32":
        return []
    monitors: list[tuple[str, QRect]] = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    @callback_type
    def callback(hmonitor, _hdc, _rect, _lparam):
        info = _MonitorInfoEx()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            rect = info.rcMonitor
            monitors.append((
                info.szDevice,
                QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top),
            ))
        return True

    try:
        user32.EnumDisplayMonitors(0, None, callback, 0)
    except Exception:
        return []
    return monitors


def _normalized_monitor_name(name: str) -> str:
    return name.lower().replace("\\\\.\\", "").strip()
