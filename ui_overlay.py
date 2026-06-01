from __future__ import annotations

import ctypes
import math
import sys
import winreg
from ctypes import wintypes
from dataclasses import dataclass


def _set_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


_set_dpi_aware()

from PyQt6.QtCore import QObject, QPointF, QRect, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget


user32 = ctypes.windll.user32

CCHDEVICENAME = 32


class _MonitorInfoEx(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * CCHDEVICENAME),
    ]


@dataclass(frozen=True)
class OverlayHighlight:
    x: int
    y: int
    width: int
    height: int
    label: str = ""
    anchor_x: int | None = None
    anchor_y: int | None = None

    @property
    def rect(self) -> QRect:
        return QRect(self.x, self.y, self.width, self.height)


def local_highlight_rect(
    highlight_rect: QRect,
    screen_geometry: QRect,
    *,
    device_pixel_ratio: float = 1.0,
    native_screen_geometry: QRect | None = None,
) -> QRect | None:
    native_screen = native_screen_geometry or _native_screen_geometry(
        screen_geometry,
        device_pixel_ratio,
    )
    if not native_screen.intersects(highlight_rect):
        return None

    dpr = _safe_device_pixel_ratio(device_pixel_ratio)
    left = math.floor((highlight_rect.x() - native_screen.x()) / dpr)
    top = math.floor((highlight_rect.y() - native_screen.y()) / dpr)
    right = math.ceil((highlight_rect.x() + highlight_rect.width() - native_screen.x()) / dpr)
    bottom = math.ceil((highlight_rect.y() + highlight_rect.height() - native_screen.y()) / dpr)
    return QRect(left, top, max(1, right - left), max(1, bottom - top))


def local_screen_point(
    x: float,
    y: float,
    screen_geometry: QRect,
    *,
    device_pixel_ratio: float = 1.0,
    native_screen_geometry: QRect | None = None,
) -> QPointF | None:
    native_screen = native_screen_geometry or _native_screen_geometry(
        screen_geometry,
        device_pixel_ratio,
    )
    if not native_screen.contains(int(x), int(y)):
        return None

    dpr = _safe_device_pixel_ratio(device_pixel_ratio)
    return QPointF(
        (float(x) - native_screen.x()) / dpr,
        (float(y) - native_screen.y()) / dpr,
    )


def highlight_visible_on_screen(
    highlight: OverlayHighlight,
    screen_geometry: QRect,
    *,
    device_pixel_ratio: float = 1.0,
    native_screen_geometry: QRect | None = None,
) -> bool:
    if highlight.anchor_x is not None and highlight.anchor_y is not None:
        return (
            local_screen_point(
                highlight.anchor_x,
                highlight.anchor_y,
                screen_geometry,
                device_pixel_ratio=device_pixel_ratio,
                native_screen_geometry=native_screen_geometry,
            )
            is not None
        )
    return (
        local_highlight_rect(
            highlight.rect,
            screen_geometry,
            device_pixel_ratio=device_pixel_ratio,
            native_screen_geometry=native_screen_geometry,
        )
        is not None
    )


def filter_highlights_for_screen(
    highlights: list[OverlayHighlight],
    screen_geometry: QRect,
    *,
    device_pixel_ratio: float = 1.0,
    native_screen_geometry: QRect | None = None,
) -> list[OverlayHighlight]:
    return [
        item
        for item in highlights
        if highlight_visible_on_screen(
            item,
            screen_geometry,
            device_pixel_ratio=device_pixel_ratio,
            native_screen_geometry=native_screen_geometry,
        )
    ]


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
    normalized_screen_name = _normalized_monitor_name(screen_name)
    if normalized_screen_name:
        for monitor_name, rect in monitors:
            if _normalized_monitor_name(monitor_name) == normalized_screen_name:
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
            monitors.append(
                (
                    info.szDevice,
                    QRect(
                        rect.left,
                        rect.top,
                        rect.right - rect.left,
                        rect.bottom - rect.top,
                    ),
                )
            )
        return True

    try:
        user32.EnumDisplayMonitors(0, None, callback, 0)
    except Exception:
        return []
    return monitors


def _normalized_monitor_name(name: str) -> str:
    return name.lower().replace("\\\\.\\", "").strip()


def place_label_rect(
    target_rect: QRect,
    *,
    label_width: int,
    label_height: int,
    surface_width: int,
    surface_height: int,
    margin: int = 8,
    gap: int = 10,
) -> QRect:
    x = target_rect.left()
    y = target_rect.top() - label_height - gap
    if y < margin:
        y = target_rect.bottom() + gap
    if y + label_height > surface_height - margin:
        y = max(margin, surface_height - label_height - margin)
    if x + label_width > surface_width - margin:
        x = surface_width - label_width - margin
    if x < margin:
        x = margin
    return QRect(x, y, label_width, label_height)


class OverlayWindow(QWidget):
    def __init__(self, screen) -> None:
        super().__init__(None)
        self._screen = screen
        self._highlights: list[OverlayHighlight] = []

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._sync_to_screen()

    def _sync_to_screen(self) -> None:
        self.setGeometry(self._screen.geometry())

    def set_highlights(self, highlights: list[OverlayHighlight]) -> None:
        self._sync_to_screen()
        geometry = self.geometry()
        dpr = screen_device_pixel_ratio(self._screen)
        native_geometry = screen_native_geometry(self._screen)
        self._highlights = filter_highlights_for_screen(
            highlights,
            geometry,
            device_pixel_ratio=dpr,
            native_screen_geometry=native_geometry,
        )
        if self._highlights:
            self.show()
            self.raise_()
            self.update()
        else:
            self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._highlights:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(event.rect())
        outline = QColor("#67e8f9")
        fill = QColor(103, 232, 249, 40)
        label_bg = QColor(15, 23, 42, 230)
        label_fg = QColor("#f8fafc")
        pen = QPen(outline, 3)
        painter.setPen(pen)
        painter.setBrush(fill)

        for highlight in self._highlights:
            local_rect = self._to_local(highlight.rect)
            painter.drawRoundedRect(local_rect, 14, 14)
            if highlight.label:
                self._draw_label(
                    painter,
                    local_rect,
                    highlight.label,
                    label_bg=label_bg,
                    label_fg=label_fg,
                    outline=outline,
                )

    def _to_local(self, rect: QRect) -> QRect:
        return local_highlight_rect(
            rect,
            self.geometry(),
            device_pixel_ratio=screen_device_pixel_ratio(self._screen),
            native_screen_geometry=screen_native_geometry(self._screen),
        ) or QRect()

    def _draw_label(
        self,
        painter: QPainter,
        rect: QRect,
        text: str,
        *,
        label_bg: QColor,
        label_fg: QColor,
        outline: QColor,
    ) -> None:
        painter.save()
        font = QFont("Segoe UI", 10)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        padding_x = 12
        padding_y = 8
        label_width = metrics.horizontalAdvance(text) + padding_x * 2
        label_height = metrics.height() + padding_y * 2

        label_rect = place_label_rect(
            rect,
            label_width=label_width,
            label_height=label_height,
            surface_width=self.width(),
            surface_height=self.height(),
        )
        painter.setPen(QPen(outline, 2))
        painter.setBrush(label_bg)
        painter.drawRoundedRect(label_rect, 10, 10)
        painter.setPen(label_fg)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class OverlayManager(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._windows: list[OverlayWindow] = []
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self.clear)
        self.refresh_windows()
        self._app.screenAdded.connect(lambda _screen: self.refresh_windows())
        self._app.screenRemoved.connect(lambda _screen: self.refresh_windows())

    def refresh_windows(self) -> None:
        existing = self._windows
        self._windows = [OverlayWindow(screen) for screen in self._app.screens()]
        for window in existing:
            window.close()
            window.deleteLater()

    def show_highlight(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        label: str = "",
        duration_ms: int = 3000,
        anchor_x: int | None = None,
        anchor_y: int | None = None,
    ) -> None:
        self.show_highlights(
            [
                OverlayHighlight(
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    label=label,
                    anchor_x=anchor_x,
                    anchor_y=anchor_y,
                )
            ],
            duration_ms=duration_ms,
        )

    def show_highlights(
        self, highlights: list[OverlayHighlight], duration_ms: int = 3000
    ) -> None:
        for window in self._windows:
            window.set_highlights(highlights)

        if duration_ms > 0:
            self._clear_timer.start(duration_ms)
        else:
            self._clear_timer.stop()

    def clear(self) -> None:
        self._clear_timer.stop()
        for window in self._windows:
            window.set_highlights([])


def _window_rect(hwnd: int) -> QRect | None:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _window_text(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _enum_child_windows(parent_hwnd: int) -> list[int]:
    result: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _lparam):
        result.append(hwnd)
        return True

    user32.EnumChildWindows(parent_hwnd, callback, 0)
    return result


def _taskbar_alignment() -> str:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "TaskbarAl")
            return "center" if value == 1 else "left"
    except OSError:
        return "left"


def _approximate_start_rect(taskbar_rect: QRect) -> QRect:
    horizontal = taskbar_rect.width() >= taskbar_rect.height()
    padding = 8

    if horizontal:
        side = max(40, min(taskbar_rect.height() - 12, 64))
        y = taskbar_rect.top() + (taskbar_rect.height() - side) // 2
        if _taskbar_alignment() == "center":
            x = taskbar_rect.center().x() - int(side * 2.2)
        else:
            x = taskbar_rect.left() + padding
        return QRect(x, y, side, side)

    side = max(40, min(taskbar_rect.width() - 12, 64))
    x = taskbar_rect.left() + (taskbar_rect.width() - side) // 2
    y = taskbar_rect.top() + padding
    return QRect(x, y, side, side)


def locate_start_button_rect() -> QRect | None:
    tray_hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    if not tray_hwnd:
        return None

    taskbar_rect = _window_rect(tray_hwnd)
    if taskbar_rect is None:
        return None

    candidates: list[tuple[int, int, QRect]] = []
    for hwnd in _enum_child_windows(tray_hwnd):
        if not user32.IsWindowVisible(hwnd):
            continue

        rect = _window_rect(hwnd)
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            continue
        if not taskbar_rect.contains(rect.center()):
            continue

        class_name = _class_name(hwnd).lower()
        text = _window_text(hwnd).lower()
        score = 0
        if "start" in class_name or "start" in text:
            score += 10
        if class_name in {"button", "start"}:
            score += 2
        if 28 <= rect.width() <= 96 and 28 <= rect.height() <= 96:
            score += 1
        if score:
            area = rect.width() * rect.height()
            candidates.append((score, area, rect))

    if candidates:
        _score, _area, rect = max(candidates, key=lambda item: (item[0], item[1]))
        return rect.adjusted(-6, -6, 6, 6)

    return _approximate_start_rect(taskbar_rect)


def _fallback_start_rect(app: QApplication) -> QRect:
    primary = app.primaryScreen()
    if primary is None:
        return QRect(8, 8, 56, 56)

    geometry = primary.geometry()
    dpr = screen_device_pixel_ratio(primary)
    width = 56
    height = 48
    x = geometry.left() + 8
    y = geometry.bottom() - height - 8
    return QRect(
        int(round(x * dpr)),
        int(round(y * dpr)),
        max(1, int(round(width * dpr))),
        max(1, int(round(height * dpr))),
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    hold = "--hold" in args

    app = QApplication(["ui_overlay.py", *args])
    overlay = OverlayManager(app)

    start_rect = locate_start_button_rect() or _fallback_start_rect(app)
    overlay.show_highlight(
        start_rect.x(),
        start_rect.y(),
        start_rect.width(),
        start_rect.height(),
        label="Start",
        duration_ms=0 if hold else 8000,
    )

    if not hold:
        QTimer.singleShot(9000, app.quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
