from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEasingCurve, QElapsedTimer, QObject, QPointF, QTimer, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient
from PyQt6.QtWidgets import QApplication, QWidget

from ui_screen_geometry import local_screen_point, screen_device_pixel_ratio, screen_native_geometry


CURSOR_AURA_RADIUS = 36.0


def paint_control_pointer(painter: QPainter, x: float, y: float) -> None:
    """Paint a black pointer with a white outline and soft purple halo."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    center = QPointF(x + 5, y + 6)
    aura = QRadialGradient(center, CURSOR_AURA_RADIUS)
    for position, alpha in ((0, 108), (.2, 88), (.45, 45), (.72, 12), (1, 0)):
        aura.setColorAt(position, QColor(199, 155, 255, alpha))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(aura)
    painter.drawEllipse(center, CURSOR_AURA_RADIUS, CURSOR_AURA_RADIUS)

    painter.translate(x, y)
    pointer = QPainterPath()
    pointer.addPolygon(QPolygonF([
        QPointF(0, 0), QPointF(15, 4.5), QPointF(7.2, 8.3), QPointF(4.2, 15),
    ]))
    pointer.closeSubpath()
    painter.translate(0, 1)
    painter.setPen(QPen(QColor(0, 0, 0, 50), 3, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(QColor(0, 0, 0, 50))
    painter.drawPath(pointer)
    painter.translate(0, -1)
    painter.setPen(QPen(QColor(255, 255, 255), 1.8, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(QColor(17, 17, 17))
    painter.drawPath(pointer)
    painter.restore()


@dataclass(frozen=True)
class GhostState:
    x: float
    y: float


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
        from capture_exclusion import exclude_from_capture
        exclude_from_capture(self)
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
        local_x = local.x()
        local_y = local.y()

        self._draw_cursor(painter, local_x, local_y)

    def _local_point(self, state: GhostState) -> QPointF | None:
        return local_screen_point(
            state.x,
            state.y,
            self.geometry(),
            device_pixel_ratio=screen_device_pixel_ratio(self._screen),
            native_screen_geometry=screen_native_geometry(self._screen),
        )

    def _draw_cursor(self, painter: QPainter, x: float, y: float) -> None:
        paint_control_pointer(painter, x, y)

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
        self._clock = QElapsedTimer()
        self._duration_ms = 0
        self._easing = QEasingCurve(QEasingCurve.Type.OutCubic)

        # Keep the handlers so close() can disconnect them: a fresh lambda
        # expression is a different object and can never be disconnected.
        self._screen_added_handler = lambda _screen: self.refresh_windows()
        self._screen_removed_handler = lambda _screen: self.refresh_windows()
        self.refresh_windows()
        self._app.screenAdded.connect(self._screen_added_handler)
        self._app.screenRemoved.connect(self._screen_removed_handler)

    def refresh_windows(self) -> None:
        existing = self._windows
        self._windows = [_GhostWindow(screen) for screen in self._app.screens()]
        for window in existing:
            window.close()
            window.deleteLater()
        if self._current is not None:
            self._broadcast(self._current)

    def show_at(self, x: int, y: int) -> None:
        self._animation_timer.stop()
        state = GhostState(x=float(x), y=float(y))
        self._current = state
        self._broadcast(state)

    def animate_to(self, x: int, y: int, duration_ms: int | None = None) -> None:
        target = QPointF(float(x), float(y))
        if self._current is None:
            self.show_at(x, y)
            return

        self._from = QPointF(self._current.x, self._current.y)
        self._to = target
        self._clock.start()
        self._duration_ms = max(60, duration_ms or self.DEFAULT_DURATION_MS)
        self._animation_timer.start()

    def clear(self) -> None:
        self._animation_timer.stop()
        self._current = None
        for window in self._windows:
            window.set_state(None)

    def close(self) -> None:
        """Release the cursor windows and screen signals (app shutdown).

        clear() only hides the cursor so later runs can reuse the windows;
        without close(), every HelplerDesktopApp construction leaked one
        top-level window per screen: the manager itself stayed alive via
        the QApplication screen-signal connections, which kept its windows
        referenced forever. ComputerUseManager already had this teardown;
        the ghost cursor did not.
        """
        self._animation_timer.stop()
        self._current = None
        for signal, handler in ((self._app.screenAdded, self._screen_added_handler),
                                (self._app.screenRemoved, self._screen_removed_handler)):
            try:
                signal.disconnect(handler)
            except (RuntimeError, TypeError):
                pass
        for window in self._windows:
            window.close()
            window.deleteLater()
        self._windows.clear()

    def _tick(self) -> None:
        # Use wall-clock elapsed time, not nominal timer intervals — QTimer
        # at 16ms is not precise, and under load each tick arrives late,
        # which would stretch the animation and distort the easing curve.
        t = min(1.0, self._clock.elapsed() / self._duration_ms)
        eased = self._easing.valueForProgress(t)
        x = self._from.x() + (self._to.x() - self._from.x()) * eased
        y = self._from.y() + (self._to.y() - self._from.y()) * eased
        state = GhostState(x=x, y=y)
        self._current = state
        self._broadcast(state)
        if t >= 1.0:
            self._animation_timer.stop()

    def _broadcast(self, state: GhostState) -> None:
        for window in self._windows:
            window.set_state(state)
