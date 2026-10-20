"""Passive, softly fading computer-use aura and status banner."""
from __future__ import annotations

from PyQt6.QtCore import QObject, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter
from PyQt6.QtWidgets import QApplication, QWidget

import theme
from capture_exclusion import exclude_from_capture


AURA_COLOR = "#c79bff"
BANNER_TEXT = "Helper is using your computer  ·  Esc to cancel"
BANNER_TOP = 38.0
BANNER_HEIGHT = 34.0
BANNER_RADIUS = 5.0


def _edge_gradient(start: QPointF, end: QPointF, opacity: float) -> QLinearGradient:
    gradient = QLinearGradient(start, end)
    # A smooth translucent wash, not a stroked border. No tint reaches the center.
    for position, alpha in ((0, 58), (.12, 46), (.35, 25), (.65, 8), (1, 0)):
        color = QColor(AURA_COLOR)
        color.setAlpha(round(alpha * opacity))
        gradient.setColorAt(position, color)
    return gradient


def banner_rect(bounds: QRectF, metrics: QFontMetricsF) -> QRectF:
    width = min(bounds.width() - 24, metrics.horizontalAdvance(BANNER_TEXT) + 24)
    return QRectF(bounds.center().x() - width / 2, bounds.top() + BANNER_TOP, width, BANNER_HEIGHT)


def paint_computer_use_frame(painter: QPainter, bounds: QRectF) -> None:
    """Diffuse lavender edge light and a violet/white banner, with no hard perimeter."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    depth = min(96.0, max(56.0, bounds.height() * .10))
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
        painter.fillRect(region, _edge_gradient(start, end, opacity))

    painter.setFont(theme.font(9, QFont.Weight.DemiBold))
    chip = banner_rect(bounds, QFontMetricsF(painter.font()))
    painter.setPen(Qt.PenStyle.NoPen)
    # A soft, very small lift behind the banner, not a dark pill or keyboard keycap.
    painter.setBrush(QColor(31, 20, 45, 18))
    painter.drawRoundedRect(chip.adjusted(-2, -1, 2, 3), BANNER_RADIUS + 1, BANNER_RADIUS + 1)
    fill = QLinearGradient(chip.topLeft(), chip.topRight())
    fill.setColorAt(0, QColor(theme.PALETTE_LIGHT["accent"]))
    fill.setColorAt(1, QColor(theme.PALETTE_LIGHT["accent_hover"]))
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
        # Like the teaching overlays, the frame is presentation, not model evidence.
        exclude_from_capture(self)

    def _sync_to_screen(self, *_args) -> None:
        self.setGeometry(self._screen.geometry())
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        paint_computer_use_frame(painter, QRectF(self.rect()))

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

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        for window in self._windows:
            if self._active:
                window.show()
                window.raise_()
            else:
                window.hide()

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
