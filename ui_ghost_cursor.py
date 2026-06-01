from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEasingCurve, QObject, QPointF, QRectF, QTimer, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PyQt6.QtWidgets import QApplication, QWidget

from ui_overlay import local_screen_point, screen_device_pixel_ratio, screen_native_geometry


CURSOR_FILL_TOP = QColor(255, 255, 255, 235)
CURSOR_FILL_BOTTOM = QColor(180, 220, 255, 235)
CURSOR_OUTLINE = QColor(20, 30, 50, 220)
CURSOR_SHADOW = QColor(0, 0, 0, 90)
BUBBLE_BG = QColor(15, 23, 42, 235)
BUBBLE_BORDER = QColor(120, 220, 200, 220)
BUBBLE_TEXT = QColor(240, 250, 248)
CURSOR_TIP_OFFSET = QPointF(2.0, 2.0)
CURSOR_SCALE = 1.8


@dataclass(frozen=True)
class GhostState:
    x: float
    y: float
    caption: str


class _GhostWindow(QWidget):
    def __init__(self, screen) -> None:
        super().__init__(None)
        self._screen = screen
        self._state: GhostState | None = None

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

    def set_state(self, state: GhostState | None) -> None:
        self._sync_to_screen()
        if state is None:
            self._state = None
            self.hide()
            return

        if self._local_point(state) is None:
            self._state = None
            self.hide()
            return

        self._state = state
        if not self.isVisible():
            self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        state = self._state
        if state is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        local = self._local_point(state)
        if local is None:
            return
        geometry = self.geometry()
        local_x = local.x()
        local_y = local.y()

        self._draw_cursor(painter, local_x, local_y)
        if state.caption:
            self._draw_bubble(painter, local_x, local_y, state.caption, geometry)

    def _local_point(self, state: GhostState) -> QPointF | None:
        return local_screen_point(
            state.x,
            state.y,
            self.geometry(),
            device_pixel_ratio=screen_device_pixel_ratio(self._screen),
            native_screen_geometry=screen_native_geometry(self._screen),
        )

    def _draw_cursor(self, painter: QPainter, x: float, y: float) -> None:
        painter.save()
        painter.translate(x - CURSOR_TIP_OFFSET.x(), y - CURSOR_TIP_OFFSET.y())
        painter.scale(CURSOR_SCALE, CURSOR_SCALE)

        path = QPainterPath()
        polygon = QPolygonF(
            [
                QPointF(0.0, 0.0),
                QPointF(0.0, 16.0),
                QPointF(4.4, 12.6),
                QPointF(7.4, 18.4),
                QPointF(9.6, 17.4),
                QPointF(6.7, 11.7),
                QPointF(11.6, 11.7),
            ]
        )
        path.addPolygon(polygon)
        path.closeSubpath()

        painter.translate(1.4, 2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(CURSOR_SHADOW)
        painter.drawPath(path)
        painter.translate(-1.4, -2.0)

        gradient = QLinearGradient(0.0, 0.0, 0.0, 18.0)
        gradient.setColorAt(0.0, CURSOR_FILL_TOP)
        gradient.setColorAt(1.0, CURSOR_FILL_BOTTOM)
        painter.setBrush(gradient)
        painter.setPen(QPen(CURSOR_OUTLINE, 1.2))
        painter.drawPath(path)

        painter.restore()

    def _draw_bubble(
        self,
        painter: QPainter,
        x: float,
        y: float,
        caption: str,
        screen_rect,
    ) -> None:
        font = QFont("Segoe UI", 10)
        font.setBold(True)
        metrics = QFontMetrics(font)
        padding_x = 14
        padding_y = 10
        max_text_width = 320
        text_width = min(max_text_width, metrics.horizontalAdvance(caption))
        line_height = metrics.height()
        lines = self._wrap(caption, metrics, text_width)
        bubble_w = max(140, min(max_text_width + padding_x * 2, max(metrics.horizontalAdvance(line) for line in lines) + padding_x * 2))
        bubble_h = line_height * len(lines) + padding_y * 2

        cursor_w = 12 * CURSOR_SCALE
        cursor_h = 18 * CURSOR_SCALE

        bubble_x = x + cursor_w * 0.7
        bubble_y = y + cursor_h * 0.6

        screen_w = screen_rect.width()
        if bubble_x + bubble_w > screen_w - 12:
            bubble_x = x - bubble_w - cursor_w * 0.4
        if bubble_x < 8:
            bubble_x = 8

        screen_h = screen_rect.height()
        if bubble_y + bubble_h > screen_h - 12:
            bubble_y = max(8, y - bubble_h - cursor_h * 0.3)

        rect = QRectF(bubble_x, bubble_y, bubble_w, bubble_h)

        painter.save()
        shadow = QRectF(rect).adjusted(0.0, 2.0, 0.0, 4.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 110))
        painter.drawRoundedRect(shadow, 12, 12)

        painter.setBrush(BUBBLE_BG)
        painter.setPen(QPen(BUBBLE_BORDER, 1.5))
        painter.drawRoundedRect(rect, 12, 12)

        painter.setFont(font)
        painter.setPen(BUBBLE_TEXT)
        text_rect = rect.adjusted(padding_x, padding_y, -padding_x, -padding_y)
        text_y = text_rect.top() + metrics.ascent()
        for line in lines:
            painter.drawText(QPointF(text_rect.left(), text_y), line)
            text_y += line_height
        painter.restore()

    @staticmethod
    def _wrap(text: str, metrics: QFontMetrics, max_width: int) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = current + " " + word
            if metrics.horizontalAdvance(candidate) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines


class GhostCursorManager(QObject):
    ANIMATION_INTERVAL_MS = 16
    DEFAULT_DURATION_MS = 520

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._windows: list[_GhostWindow] = []
        self._current: GhostState | None = None
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(self.ANIMATION_INTERVAL_MS)
        self._animation_timer.timeout.connect(self._tick)

        self._from = QPointF(0.0, 0.0)
        self._to = QPointF(0.0, 0.0)
        self._elapsed_ms = 0
        self._duration_ms = 0
        self._caption = ""
        self._easing = QEasingCurve(QEasingCurve.Type.OutCubic)

        self.refresh_windows()
        self._app.screenAdded.connect(lambda _screen: self.refresh_windows())
        self._app.screenRemoved.connect(lambda _screen: self.refresh_windows())

    def refresh_windows(self) -> None:
        existing = self._windows
        self._windows = [_GhostWindow(screen) for screen in self._app.screens()]
        for window in existing:
            window.close()
            window.deleteLater()
        if self._current is not None:
            self._broadcast(self._current)

    def show_at(self, x: int, y: int, caption: str = "") -> None:
        self._animation_timer.stop()
        self._caption = caption
        state = GhostState(x=float(x), y=float(y), caption=caption)
        self._current = state
        self._broadcast(state)

    def animate_to(
        self,
        x: int,
        y: int,
        caption: str = "",
        duration_ms: int | None = None,
    ) -> None:
        target = QPointF(float(x), float(y))
        if self._current is None:
            self.show_at(x, y, caption)
            return

        self._from = QPointF(self._current.x, self._current.y)
        self._to = target
        self._caption = caption
        self._elapsed_ms = 0
        self._duration_ms = max(60, duration_ms or self.DEFAULT_DURATION_MS)
        self._animation_timer.start()

    def update_caption(self, caption: str) -> None:
        if self._current is None:
            return
        self._caption = caption
        state = GhostState(x=self._current.x, y=self._current.y, caption=caption)
        self._current = state
        self._broadcast(state)

    def clear(self) -> None:
        self._animation_timer.stop()
        self._current = None
        for window in self._windows:
            window.set_state(None)

    def _tick(self) -> None:
        self._elapsed_ms += self.ANIMATION_INTERVAL_MS
        t = min(1.0, self._elapsed_ms / self._duration_ms)
        eased = self._easing.valueForProgress(t)
        x = self._from.x() + (self._to.x() - self._from.x()) * eased
        y = self._from.y() + (self._to.y() - self._from.y()) * eased
        state = GhostState(x=x, y=y, caption=self._caption)
        self._current = state
        self._broadcast(state)
        if t >= 1.0:
            self._animation_timer.stop()

    def _broadcast(self, state: GhostState) -> None:
        for window in self._windows:
            window.set_state(state)


def _demo() -> int:
    import sys

    app = QApplication(sys.argv)
    manager = GhostCursorManager(app)
    primary = app.primaryScreen()
    screen = primary.geometry()
    dpr = screen_device_pixel_ratio(primary)
    cx = int((screen.left() + screen.width() // 2) * dpr)
    cy = int((screen.top() + screen.height() // 2) * dpr)
    manager.show_at(cx - 200, cy - 80, "Step 1: Click the Start button to open the menu.")
    QTimer.singleShot(1500, lambda: manager.animate_to(cx + 220, cy + 40, "Step 2: Type 'cmd' to find Command Prompt."))
    QTimer.singleShot(3500, lambda: manager.animate_to(cx - 120, cy + 200, "Step 3: Press Enter to launch it."))
    QTimer.singleShot(6000, manager.clear)
    QTimer.singleShot(7000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(_demo())
