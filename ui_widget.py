import math
import sys

from PyQt6.QtCore import QElapsedTimer, QEvent, QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QLineEdit, QMenu, QWidget

import theme
from ui_lava import paint_lava_orb

class FloatingCircle(QWidget):
    muted_changed = pyqtSignal(bool)
    state_changed = pyqtSignal(str)
    quit_requested = pyqtSignal()
    restart_requested = pyqtSignal()
    chat_requested = pyqtSignal()
    chat_submitted = pyqtSignal(str)
    chat_open_changed = pyqtSignal(bool)
    dashboard_requested = pyqtSignal()
    moved = pyqtSignal()
    voice_hold_started = pyqtSignal()
    voice_hold_finished = pyqtSignal()

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"

    CHAT_PLACEHOLDER = "Ask anything or give me a task…"

    WIDGET_W = 124
    WIDGET_W_OPEN = 360
    WIDGET_H = 40
    PILL_INSET = 2.0
    ORB_CX = 62.0
    ORB_CX_OPEN = 24.0
    ORB_CY = 20.0
    ORB_BASE_R = 16.0
    ORB_BASE_R_OPEN = 16.0
    LEFT_CX = 20.0
    LEFT_CY = 20.0
    LEFT_R = 12.0
    RIGHT_CX = 104.0
    RIGHT_CY = 20.0
    RIGHT_R = 12.0

    ZONE_LEFT = "left"
    ZONE_ORB = "orb"
    ZONE_RIGHT = "right"

    HOLD_THRESHOLD_MS = 300

    def __init__(self) -> None:
        super().__init__()
        self.state = self.IDLE
        self.muted = False
        self.caption: str = ""
        self._drag_offset: QPoint | None = None
        self._click_pos = None
        self._click_zone: str | None = None

        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_time = 0.0
        self.level_smoothed = 0.0

        self._press_amt = 0.0
        self._press_target = 0.0
        self._press_axis = (0.0, -1.0)
        self._hover_amt = 0.0
        self._hover_target = 0.0
        self._open_amt = 0.0
        self._open_target = 0.0
        self._last_window_pos: QPoint | None = None
        self._was_dragging = False
        self._voice_hold_active = False
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(self.HOLD_THRESHOLD_MS)
        self._hold_timer.timeout.connect(self._on_hold_threshold_reached)

        self._frame_interval_active = 16
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(self._frame_interval_active)
        self._frame_timer.timeout.connect(self.update)

        self.setFixedSize(self.WIDGET_W, self.WIDGET_H)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setWindowTitle("Helper")

        self._chat_input = QLineEdit(self)
        self._chat_input.setPlaceholderText(self.CHAT_PLACEHOLDER)
        self._chat_input.setFont(QFont("Segoe UI", 10))
        self._chat_input.setStyleSheet(theme.qss("""
            QLineEdit {{ background: transparent; border: none; color: {text_primary};
                padding: 0; selection-background-color: {accent}; selection-color: {selection_fg}; }}
        """))
        self._chat_input.setAccessibleName("Message Helper")
        self.setToolTip("Click to chat · Hold to speak · Right-click for settings")
        self._chat_input.returnPressed.connect(self._on_chat_input_submit)
        self._chat_input.installEventFilter(self)
        self._chat_input.hide()

    def _wake_frame_timer(self) -> None:
        """Wake the animation timer for a visible widget."""
        if self.isVisible() and not self._frame_timer.isActive():
            self._frame_timer.setInterval(self._frame_interval_active)
            self._frame_timer.start()

    def set_state(self, state: str) -> None:
        normalized = state.lower()
        if normalized not in {self.IDLE, self.LISTENING, self.THINKING, self.SPEAKING}:
            raise ValueError(f"Unsupported widget state: {state}")
        if normalized == self.state:
            return
        self._wake_frame_timer()
        self.state = normalized
        self.state_changed.emit(self.state)
        self.update()

    def set_caption(self, text: str) -> None:
        normalized = (text or "").strip()
        if normalized == self.caption:
            return
        self.caption = normalized
        self.update()

    def set_muted(self, muted: bool) -> None:
        if muted == self.muted:
            return
        self.muted = muted
        self.muted_changed.emit(self.muted)
        self.update()

    def set_chat_open(self, opened: bool) -> None:
        already_open = self._open_target > 0.5
        if opened == already_open:
            return
        self._wake_frame_timer()
        self._open_target = 1.0 if opened else 0.0
        if opened:
            self.setFixedSize(self.WIDGET_W_OPEN, self.WIDGET_H)
            self._update_chat_input_geometry()
            self._chat_input.clear()
            self._chat_input.show()
            self._chat_input.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self._chat_input.hide()
        self.chat_open_changed.emit(opened)

    def toggle_chat_open(self) -> None:
        self.set_chat_open(not self.is_chat_open())

    def is_chat_open(self) -> bool:
        return self._open_target > 0.5

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is self._chat_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.set_chat_open(False)
                return True
        return super().eventFilter(obj, event)

    def _effective_orb_x(self) -> float:
        return self.ORB_CX + (self.ORB_CX_OPEN - self.ORB_CX) * self._open_amt

    def _update_chat_input_geometry(self) -> None:
        left = int(self._effective_orb_x() + self.ORB_BASE_R + 8)
        right_margin = 14
        self._chat_input.setGeometry(
            left,
            6,
            max(40, self.width() - left - right_margin),
            self.WIDGET_H - 12,
        )

    def _on_chat_input_submit(self) -> None:
        text = self._chat_input.text().strip()
        if not text:
            return
        self._chat_input.clear()
        self.chat_submitted.emit(text)

    def toggle_muted(self) -> None:
        self.set_muted(not self.muted)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self._wake_frame_timer()
        if event.button() == Qt.MouseButton.LeftButton:
            local = event.position()
            zone = self._hit_zone(local)
            if zone is None:
                event.accept()
                return
            self._click_zone = zone
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._click_pos = event.globalPosition()
            if zone == self.ZONE_ORB:
                self._hold_timer.start()
            self._last_window_pos = self.frameGeometry().topLeft()
            self._was_dragging = False
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            if self._hit_zone(event.position()) is None:
                event.accept()
                return
            self._show_context_menu(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def _hit_zone(self, local_point: QPointF) -> str | None:
        x, y = local_point.x(), local_point.y()
        expanded = self._hover_amt > 0.6
        chat_open = self._open_amt > 0.3 or self._open_target >= 1.0
        orb_cx = self._effective_orb_x()
        if chat_open:
            if math.hypot(x - orb_cx, y - self.ORB_CY) <= self.ORB_BASE_R + 4:
                return self.ZONE_ORB
            return None
        if expanded:
            if math.hypot(x - self.LEFT_CX, y - self.LEFT_CY) <= self.LEFT_R + 2:
                return self.ZONE_LEFT
            if math.hypot(x - self.RIGHT_CX, y - self.RIGHT_CY) <= self.RIGHT_R + 2:
                return self.ZONE_RIGHT
            return self.ZONE_ORB
        if math.hypot(x - orb_cx, y - self.ORB_CY) <= self.ORB_BASE_R + 4:
            return self.ZONE_ORB
        return None

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._wake_frame_timer()
        self._update_hover(event.position())
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._wake_frame_timer()
        self._hover_target = 0.0
        super().leaveEvent(event)

    def _update_hover(self, local_point: QPointF) -> None:
        if self._hover_target >= 0.5:
            return
        orb_cx = self._effective_orb_x()
        over_orb = math.hypot(
            local_point.x() - orb_cx,
            local_point.y() - self.ORB_CY,
        ) <= self.ORB_BASE_R + 4
        if over_orb:
            self._hover_target = 1.0

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if self._voice_hold_active:
                event.accept()
                return
            new_top_left = event.globalPosition().toPoint() - self._drag_offset
            if self._last_window_pos is not None:
                dx = new_top_left.x() - self._last_window_pos.x()
                dy = new_top_left.y() - self._last_window_pos.y()
                if dx or dy:
                    if not self._was_dragging:
                        self._press_target = 0.0
                        self._hold_timer.stop()
                    self._was_dragging = True
            self._last_window_pos = new_top_left
            self.move(new_top_left)
            event.accept()
            return
        self._update_hover(event.position())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._wake_frame_timer()
        if event.button() == Qt.MouseButton.LeftButton:
            self._hold_timer.stop()
            if self._voice_hold_active:
                self._voice_hold_active = False
                self.voice_hold_finished.emit()
            elif getattr(self, '_click_pos', None) is not None and not self._was_dragging:
                dist = (event.globalPosition() - self._click_pos).manhattanLength()
                if dist < 5:
                    if self._click_zone in {self.ZONE_LEFT, self.ZONE_ORB}:
                        self.chat_requested.emit()
                    elif self._click_zone == self.ZONE_RIGHT:
                        self.toggle_muted()
            self._press_target = 0.0
            self._drag_offset = None
            self._click_pos = None
            self._click_zone = None
            self._last_window_pos = None
            self._was_dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _on_hold_threshold_reached(self) -> None:
        if (
            self._click_zone == self.ZONE_ORB
            and not self._was_dragging
            and self._drag_offset is not None
        ):
            self._voice_hold_active = True
            self._press_target = 0.0
            self.voice_hold_started.emit()

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        self.moved.emit()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        now = self._clock.elapsed() / 1000.0
        dt = min(0.05, now - self._last_time)
        if self._last_time == 0.0:
            dt = 0.016
        self._last_time = now
        t = now

        self._press_amt += (self._press_target - self._press_amt) * min(1.0, dt * 14.0)
        self._hover_amt += (self._hover_target - self._hover_amt) * min(1.0, dt * 11.0)
        self._open_amt += (self._open_target - self._open_amt) * min(1.0, dt * 11.0)
        if self.is_chat_open():
            self._update_chat_input_geometry()

        if (
            self._open_target <= 0.0
            and self._open_amt < 0.02
            and self.width() != self.WIDGET_W
        ):
            self.setFixedSize(self.WIDGET_W, self.WIDGET_H)
        elif (
            self._open_target >= 1.0
            and self.width() != self.WIDGET_W_OPEN
        ):
            self.setFixedSize(self.WIDGET_W_OPEN, self.WIDGET_H)
            self._update_chat_input_geometry()

        animating = (
            self.state != self.IDLE
            or abs(self._press_amt - self._press_target) > 0.005
            or abs(self._hover_amt - self._hover_target) > 0.005
            or abs(self._open_amt - self._open_target) > 0.005
        )
        # Keep the fluid moving at a quiet 30fps even while idle. Interaction
        # runs at 60fps; hidden windows stop their timer in hideEvent.
        interval = self._frame_interval_active if animating else 33
        if self._frame_timer.interval() != interval:
            self._frame_timer.setInterval(interval)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        self._paint_pill_background(painter, self._hover_amt, self._open_amt)
        cx, cy = self._effective_orb_x(), self.ORB_CY
        energy = {self.IDLE: 0, self.LISTENING: .35, self.THINKING: .55,
                  self.SPEAKING: .8}.get(self.state, 0)
        radius = self.ORB_BASE_R * (1 + .018 * math.sin(t * 1.4))
        painter.save()
        painter.translate(cx, cy)
        painter.scale(1 + self._press_amt * .10, 1 - self._press_amt * .15)
        paint_lava_orb(painter, QRectF(-radius, -radius, radius * 2, radius * 2), t, energy=energy)
        painter.restore()
        self._paint_left_icon(painter, self._hover_amt)
        self._paint_right_icon(painter, self._hover_amt)
        if self.state != self.IDLE:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.color('accent'))
            offset = radius * .7
            dot_radius = radius * .13
            painter.drawEllipse(QPointF(cx + offset, cy + offset), dot_radius, dot_radius)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._frame_timer.start(33)

    def hideEvent(self, event) -> None:
        self._frame_timer.stop()
        super().hideEvent(event)

    def _paint_pill_background(self, painter: QPainter, hover_amt: float, open_amt: float = 0.0) -> None:
        combined = max(hover_amt, open_amt)
        if combined < 0.005:
            return

        widget_w = float(self.width())
        full_width = widget_w - 2 * self.PILL_INSET
        collapsed_width = self.WIDGET_H - 2 * self.PILL_INSET
        width = collapsed_width + (full_width - collapsed_width) * combined
        if open_amt > 0.001:
            left_anchor = self.PILL_INSET
            center_x = left_anchor + width / 2.0
        else:
            center_x = widget_w / 2.0
        left = center_x - width / 2.0

        rect = QRectF(
            left,
            self.PILL_INSET,
            width,
            self.WIDGET_H - 2 * self.PILL_INSET,
        )
        radius = rect.height() / 2.0

        alpha_scale = min(1.0, combined * 2.2)

        painter.save()
        painter.setOpacity(alpha_scale)
        painter.setPen(Qt.PenStyle.NoPen)

        painter.setBrush(theme.color('bg_card'))
        painter.drawRoundedRect(rect, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(theme.color('border_mid'), 1.0))
        painter.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), radius, radius)
        painter.restore()

    def _paint_left_icon(self, painter: QPainter, hover_amt: float) -> None:
        alpha = max(0.0, min(1.0, (hover_amt - .4) / .5)) * (1 - self._open_amt)
        if alpha < .01:
            return
        painter.save()
        painter.setOpacity(alpha)
        cx, cy = self.LEFT_CX, self.LEFT_CY
        painter.translate(cx, cy)
        painter.scale(self.LEFT_R / 16, self.LEFT_R / 16)
        cx, cy = 0, 0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color('accent_subtle'))
        painter.drawEllipse(QPointF(cx, cy), 16, 16)
        painter.setPen(QPen(theme.color('accent'), 1.8, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        bubble = QPainterPath(QPointF(-4, 5))
        bubble.lineTo(-8, 9)
        bubble.lineTo(-8, -4)
        bubble.quadTo(-8, -7, -5, -7)
        bubble.lineTo(5, -7)
        bubble.quadTo(8, -7, 8, -4)
        bubble.lineTo(8, 2)
        bubble.quadTo(8, 5, 5, 5)
        bubble.closeSubpath()
        painter.drawPath(bubble)
        painter.drawLine(QPointF(-4, -2), QPointF(4, -2))
        painter.drawLine(QPointF(-4, 1), QPointF(1, 1))
        painter.restore()

    def _paint_right_icon(self, painter: QPainter, hover_amt: float) -> None:
        alpha = max(0.0, min(1.0, (hover_amt - .4) / .5)) * (1 - self._open_amt)
        if alpha < .01:
            return
        painter.save()
        painter.setOpacity(alpha)
        cx, cy = self.RIGHT_CX, self.RIGHT_CY
        painter.translate(cx, cy)
        painter.scale(self.RIGHT_R / 16, self.RIGHT_R / 16)
        cx, cy = 0, 0
        painter.setBrush(theme.color('accent_subtle'))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), 16, 16)
        painter.setPen(QPen(theme.color('text_secondary'), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(cx - 3, cy - 8, 6, 11), 3, 3)
        painter.drawArc(QRectF(cx - 6, cy - 5, 12, 12), 180 * 16, 180 * 16)
        painter.drawLine(QPointF(cx, cy + 7), QPointF(cx, cy + 10))
        painter.drawLine(QPointF(cx - 3, cy + 10), QPointF(cx + 3, cy + 10))
        if self.muted:
            painter.setPen(QPen(theme.color('danger_fg'), 1.8))
            painter.drawLine(QPointF(cx - 8, cy + 8), QPointF(cx + 8, cy - 8))
        painter.restore()

    def _paint_label(self, painter: QPainter, rect: QRectF, accent: QColor) -> None:
        if self.caption:
            self._paint_caption(painter)
            return

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 235))
        painter.drawRoundedRect(rect, 12, 12)

        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 100), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)

    def _paint_caption(self, painter: QPainter) -> None:
        margin = 6
        rect = QRectF(margin, 116, self.width() - margin * 2, 28)

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 28))
        painter.drawRoundedRect(rect, 12, 12)

        highlight = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        highlight.setColorAt(0.0, QColor(255, 255, 255, 36))
        highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(highlight)
        painter.drawRoundedRect(rect, 12, 12)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)

        font = QFont("Segoe UI", 8)
        font.setItalic(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        text_rect = rect.adjusted(10, 0, -10, 0)
        elided = metrics.elidedText(
            self.caption,
            Qt.TextElideMode.ElideLeft,
            int(text_rect.width()),
        )
        painter.setPen(QColor(245, 245, 245, 230))
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter), elided)
        painter.restore()

    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(theme.qss(theme.MENU_QSS))

        chat_action = menu.addAction("Open chat")
        chat_action.triggered.connect(self.chat_requested.emit)

        mute_action = QAction("Unmute" if self.muted else "Mute", self)
        mute_action.triggered.connect(self.toggle_muted)
        menu.addAction(mute_action)

        menu.addSeparator()

        preview_actions = {
            "Preview Idle": self.IDLE,
            "Preview Listening": self.LISTENING,
            "Preview Thinking": self.THINKING,
            "Preview Speaking": self.SPEAKING,
        }
        for label, state in preview_actions.items():
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, value=state: self.set_state(value))
            menu.addAction(action)

        menu.addSeparator()

        dashboard_action = QAction("Dashboard", self)
        dashboard_action.triggered.connect(self.dashboard_requested.emit)
        menu.addAction(dashboard_action)

        restart_action = QAction("Restart Helper", self)
        restart_action.triggered.connect(self._handle_restart_requested)
        menu.addAction(restart_action)

        quit_action = QAction("Quit Helper", self)
        quit_action.triggered.connect(self._handle_quit_requested)
        menu.addAction(quit_action)

        menu.exec(global_pos)
        menu.deleteLater()

    def _handle_restart_requested(self) -> None:
        self.restart_requested.emit()

    def _handle_quit_requested(self) -> None:
        self.quit_requested.emit()
        app = QApplication.instance()
        if app is not None:
            app.quit()


class ChatStatusPill(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._text = ""
        self._display = ""
        self._font = QFont("Segoe UI", 9)
        self._padding_x = 12
        self._padding_y = 5
        self._max_width = 420
        self._max_lines = 4
        self.setFixedHeight(24)
        self.setFixedWidth(220)
        self.hide()

    def text(self) -> str:
        return self._text

    def _text_rect(self) -> QRectF:
        return QRectF(self._padding_x, self._padding_y,
                      max(1, self.width() - 2 * self._padding_x), max(1, self.height() - 2 * self._padding_y))

    def set_text(self, text: str) -> None:
        text = " ".join((text or "").split())
        if text == self._text:
            if not text:
                self.hide()
            return
        self._text = text
        if not text:
            self._display = ""
            self.hide()
            return
        metrics = QFontMetrics(self._font)
        text_w = metrics.horizontalAdvance(text)
        width = max(120, min(self._max_width, text_w + 2 * self._padding_x + 2))
        inner = width - 2 * self._padding_x
        bounds = metrics.boundingRect(0, 0, inner, 10_000, int(Qt.TextFlag.TextWordWrap), text)
        lines = max(1, min(self._max_lines, math.ceil(bounds.height() / max(1, metrics.lineSpacing()))))
        height = lines * metrics.lineSpacing()
        self._display = self._fit_text(text, metrics, inner, height)
        self.setFixedWidth(width)
        self.setFixedHeight(max(24, height + 2 * self._padding_y))
        self.update()
        if not self.isVisible():
            self.show()

    @staticmethod
    def _fit_text(text: str, metrics: QFontMetrics, width: int, height: int) -> str:
        def fits(candidate: str) -> bool:
            return metrics.boundingRect(0, 0, width, 10_000, int(Qt.TextFlag.TextWordWrap),
                                        candidate).height() <= height + 1
        if fits(text):
            return text
        # The status pill is a preview; the complete reply stays in the chat transcript.
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if fits(text[:middle].rstrip() + "…"):
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + "…"

    def anchor_above(self, orb_widget: QWidget) -> None:
        if orb_widget is None:
            return
        orb_geom = orb_widget.frameGeometry()
        x = orb_geom.center().x() - self.width() // 2
        y = orb_geom.top() - self.height() - 6
        self.move(x, y)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        radius = min(12.0, rect.height() / 2.0)

        shadow = QRectF(rect).adjusted(0.0, 1.5, 0.0, 3.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.drawRoundedRect(shadow, radius, radius)

        painter.setBrush(theme.color('bg_card'))
        painter.drawRoundedRect(rect, radius, radius)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(theme.color('border_mid'), 1.0))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)

        if self._display:
            painter.setFont(self._font)
            painter.setPen(theme.color('text_primary'))
            painter.drawText(
                self._text_rect(),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                self._display,
            )


def _run_demo() -> int:
    app = QApplication(sys.argv)
    widget = FloatingCircle()
    widget.chat_requested.connect(widget.toggle_chat_open)
    widget.move(80, 80)
    widget.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(_run_demo())
