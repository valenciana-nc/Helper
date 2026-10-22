"""Passive, breathing purple computer-use aura and glowing status banner."""
from __future__ import annotations

import math

from PyQt6.QtCore import QElapsedTimer, QObject, QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QRegion
from PyQt6.QtWidgets import QApplication, QWidget

import theme
from capture_exclusion import exclude_from_capture


# Intentional purple presentation, independent of the monochrome dashboard/theme accents.
AURA_COLOR = "#c79bff"
AURA_PEAK_COLOR = "#b478ff"
PULSE_SECONDS = 4.2
ANIMATION_INTERVAL_MS = 33
BANNER_COLOR = "#9254de"
BANNER_END_COLOR = "#7b3dcc"
BANNER_TEXT = "Helper is using your computer  ·  Esc to cancel"
BANNER_TOP = 38.0
BANNER_HEIGHT = 34.0
BANNER_RADIUS = 5.0
BANNER_MAX_GLOW = 16


def aura_pulse(seconds: float) -> float:
    """A seamless low → high → low breath, starting at the original faint state."""
    return (1 - math.cos(math.tau * (max(0.0, seconds) % PULSE_SECONDS) / PULSE_SECONDS)) / 2


def _aura_depth(bounds: QRectF, pulse: float) -> float:
    base = min(96.0, max(56.0, bounds.height() * .10))
    return min(base * (1 + .55 * pulse), min(bounds.width(), bounds.height()) * .24)


def _edge_gradient(start: QPointF, end: QPointF, opacity: float, pulse: float) -> QLinearGradient:
    gradient = QLinearGradient(start, end)
    low, high = QColor(AURA_COLOR), QColor(AURA_PEAK_COLOR)
    shade = QColor(*(round(a + (b - a) * pulse) for a, b in zip(low.getRgb()[:3], high.getRgb()[:3])))
    # Expand and intensify a diffuse wash, never a hard stroked border or a center tint.
    for position, alpha in ((0, 58), (.12, 46), (.35, 25), (.65, 8), (1, 0)):
        color = QColor(shade)
        color.setAlpha(round(alpha * opacity * (1 + .8 * pulse)))
        gradient.setColorAt(position, color)
    return gradient


def banner_rect(bounds: QRectF, metrics: QFontMetricsF) -> QRectF:
    width = min(bounds.width() - 24, metrics.horizontalAdvance(BANNER_TEXT) + 24)
    return QRectF(bounds.center().x() - width / 2, bounds.top() + BANNER_TOP, width, BANNER_HEIGHT)


def _animation_region(bounds: QRectF, metrics: QFontMetricsF) -> QRegion:
    """Only repaint the maximum edge strips and banner halo, not the desktop center."""
    rect = bounds.toAlignedRect()
    depth = math.ceil(_aura_depth(bounds, 1)) + 1
    region = QRegion(QRect(rect.left(), rect.top(), rect.width(), depth))
    region |= QRegion(QRect(rect.left(), rect.bottom() - depth + 1, rect.width(), depth))
    region |= QRegion(QRect(rect.left(), rect.top(), depth, rect.height()))
    region |= QRegion(QRect(rect.right() - depth + 1, rect.top(), depth, rect.height()))
    halo = BANNER_MAX_GLOW + 2
    return region | QRegion(banner_rect(bounds, metrics).adjusted(-halo, -halo, halo, halo).toAlignedRect())


def paint_computer_use_frame(painter: QPainter, bounds: QRectF, seconds: float = 0.0) -> None:
    """Breathing purple edge light and a purple/white glowing banner; transparent center."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pulse = aura_pulse(seconds)
    depth = _aura_depth(bounds, pulse)
    for region, start, end, opacity in (
        (QRectF(bounds.left(), bounds.top(), bounds.width(), depth),
         bounds.topLeft(), bounds.topLeft() + QPointF(0, depth), 1.0),
        (QRectF(bounds.left(), bounds.bottom() - depth, bounds.width(), depth),
         bounds.bottomLeft(), bounds.bottomLeft() - QPointF(0, depth), .85),
        (QRectF(bounds.left(), bounds.top(), depth, bounds.height()),
         bounds.topLeft(), bounds.topLeft() + QPointF(depth, 0), .75),
        (QRectF(bounds.right() - depth, bounds.top(), depth, bounds.height()),
         bounds.topRight(), bounds.topRight() - QPointF(depth, 0), .75),
    ):
        painter.fillRect(region, _edge_gradient(start, end, opacity, pulse))

    painter.setFont(theme.font(9, QFont.Weight.DemiBold))
    chip = banner_rect(bounds, QFontMetricsF(painter.font()))
    painter.setPen(Qt.PenStyle.NoPen)
    # Small nested translucent layers approximate a soft bloom without a full-screen blur.
    spread = 10 + (BANNER_MAX_GLOW - 10) * pulse
    for expansion in range(math.ceil(spread), 0, -1):
        glow = QColor(AURA_COLOR)
        glow.setAlpha(round(1 + (4 + 3 * pulse) * (1 - expansion / spread) ** 2))
        painter.setBrush(glow)
        painter.drawRoundedRect(chip.adjusted(-expansion, -expansion, expansion, expansion),
                                BANNER_RADIUS + expansion, BANNER_RADIUS + expansion)
    fill = QLinearGradient(chip.topLeft(), chip.topRight())
    fill.setColorAt(0, QColor(BANNER_COLOR))
    fill.setColorAt(1, QColor(BANNER_END_COLOR))
    painter.setBrush(fill)
    painter.drawRoundedRect(chip, BANNER_RADIUS, BANNER_RADIUS)
    painter.setPen(QColor("#ffffff"))
    text = QFontMetricsF(painter.font()).elidedText(
        BANNER_TEXT, Qt.TextElideMode.ElideRight, max(1.0, chip.width() - 20))
    painter.drawText(chip.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignCenter, text)
    painter.restore()


class ComputerUseWindow(QWidget):
    def __init__(self, screen) -> None:
        super().__init__()
        self._screen = screen
        self._animation_seconds = 0.0
        self.setWindowTitle("Helper computer use")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._sync_to_screen()
        screen.geometryChanged.connect(self._sync_to_screen)
        # The frame is presentation, not model evidence.
        exclude_from_capture(self)

    def _sync_to_screen(self, *_args) -> None:
        self.setGeometry(self._screen.geometry())
        self._animated_region = _animation_region(QRectF(self.rect()), QFontMetricsF(theme.font(9, QFont.Weight.DemiBold)))
        self.update()

    def set_animation_time(self, seconds: float) -> None:
        if seconds != self._animation_seconds:
            self._animation_seconds = seconds
            self.update(self._animated_region)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        paint_computer_use_frame(painter, QRectF(self.rect()), self._animation_seconds)

    def closeEvent(self, event) -> None:
        try:
            self._screen.geometryChanged.disconnect(self._sync_to_screen)
        except (RuntimeError, TypeError):
            pass  # A removed monitor can already have been destroyed by Qt.
        super().closeEvent(event)


class ComputerUseManager(QObject):
    def __init__(self, app: QApplication, parent=None) -> None:
        super().__init__(parent)
        self._app = app
        self._active = False
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(ANIMATION_INTERVAL_MS)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._advance_animation)
        self._windows: list[ComputerUseWindow] = []
        self.refresh_windows()
        app.screenAdded.connect(self.refresh_windows)
        app.screenRemoved.connect(self.refresh_windows)

    @property
    def active(self) -> bool:
        return self._active

    def refresh_windows(self, *_args) -> None:
        old = self._windows
        self._windows = [ComputerUseWindow(screen) for screen in self._app.screens()]
        for window in old:
            window.close()
            window.deleteLater()
        self.set_active(self._active)

    def _advance_animation(self) -> None:
        if self._active:
            seconds = self._clock.elapsed() / 1000
            for window in self._windows:
                window.set_animation_time(seconds)

    def set_active(self, active: bool) -> None:
        was_active = self._active
        self._active = bool(active)
        if self._active:
            if not was_active:
                self._clock.start()
                self._timer.start()
            self._advance_animation()
        else:
            self._timer.stop()
            self._clock.invalidate()
        for window in self._windows:
            if self._active:
                window.show()
                window.raise_()
            else:
                window.hide()
                window.set_animation_time(0.0)

    def close(self) -> None:
        self.set_active(False)
        for signal in (self._app.screenAdded, self._app.screenRemoved):
            try:
                signal.disconnect(self.refresh_windows)
            except (RuntimeError, TypeError):
                pass
        for window in self._windows:
            window.close()
            window.deleteLater()
        self._windows.clear()
