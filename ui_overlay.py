from __future__ import annotations

import ctypes
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

from PyQt6.QtCore import QObject, QRect, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget


user32 = ctypes.windll.user32


@dataclass(frozen=True)
class OverlayHighlight:
    x: int
    y: int
    width: int
    height: int
    label: str = ""

    @property
    def rect(self) -> QRect:
        return QRect(self.x, self.y, self.width, self.height)


def local_highlight_rect(highlight_rect: QRect, screen_geometry: QRect) -> QRect | None:
    if not screen_geometry.intersects(highlight_rect):
        return None
    return highlight_rect.translated(-screen_geometry.x(), -screen_geometry.y())


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
        self._highlights = [
            item for item in highlights if local_highlight_rect(item.rect, geometry) is not None
        ]
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
        return local_highlight_rect(rect, self.geometry()) or QRect()

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
    ) -> None:
        self.show_highlights(
            [OverlayHighlight(x=x, y=y, width=width, height=height, label=label)],
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
    width = 56
    height = 48
    x = geometry.left() + 8
    y = geometry.bottom() - height - 8
    return QRect(x, y, width, height)


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
