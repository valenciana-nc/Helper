import math
import sys
import random

from PyQt6.QtCore import QElapsedTimer, QEvent, QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QFontMetrics, QKeyEvent, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient, QPolygonF
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLineEdit, QMenu, QWidget

def hash2(x: float, y: float) -> float:
    s = math.sin(x * 127.1 + y * 311.7) * 43758.5453
    return s - math.floor(s)

def smooth(t: float) -> float:
    return t * t * (3 - 2 * t)

def noise2(x: float, y: float) -> float:
    xi = math.floor(x)
    yi = math.floor(y)
    xf = x - xi
    yf = y - yi
    a = hash2(xi, yi)
    b = hash2(xi + 1, yi)
    c = hash2(xi, yi + 1)
    d = hash2(xi + 1, yi + 1)
    u = smooth(xf)
    v = smooth(yf)
    return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v

def wobble(angle: float, t: float) -> float:
    return (
        0.45 * math.sin(angle * 3 + t * 0.7) +
        0.30 * math.sin(angle * 5 + t * 1.1 + 1.3) +
        0.25 * math.sin(angle * 7 + t * 0.5 + 2.7)
    )

def mix_color(c1: QColor, c2: QColor, t: float) -> QColor:
    return QColor(
        int(c1.red() + (c2.red() - c1.red()) * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue() + (c2.blue() - c1.blue()) * t),
        int(c1.alpha() + (c2.alpha() - c1.alpha()) * t),
    )

class FloatingCircle(QWidget):
    mode_toggle_requested = pyqtSignal()
    mode_select_requested = pyqtSignal(str)
    mode_changed = pyqtSignal(str)
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

    HELP = "help"
    ACTIVE = "active"
    MODE_ORDER = ("help", "active")
    HELP_CHAT_PLACEHOLDER = "Ask helper how to..."
    ACTIVE_CHAT_PLACEHOLDER = "Tell Helper what to do..."

    WIDGET_W = 124
    WIDGET_W_OPEN = 440
    WIDGET_H = 40
    PILL_INSET = 2.0
    ORB_CX = 62.0
    ORB_CX_OPEN = 24.0
    ORB_CY = 20.0
    ORB_BASE_R = 16.0
    ORB_BASE_R_OPEN = 16.0
    LEFT_CX = 20.0
    LEFT_CY = 20.0
    LEFT_R = 11.0
    RIGHT_CX = 104.0
    RIGHT_CY = 20.0
    RIGHT_R = 11.0

    ZONE_LEFT = "left"
    ZONE_ORB = "orb"
    ZONE_RIGHT = "right"

    HOLD_THRESHOLD_MS = 300

    def __init__(self) -> None:
        super().__init__()
        self.state = self.IDLE
        self.mode = self.HELP
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
        self._ripples: list[dict] = []
        self._voice_hold_active = False
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(self.HOLD_THRESHOLD_MS)
        self._hold_timer.timeout.connect(self._on_hold_threshold_reached)

        self._frame_interval_active = 16
        self._frame_interval_idle = 33
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(self._frame_interval_active)
        self._frame_timer.timeout.connect(self.update)
        self._frame_timer.start()

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
        self._chat_input.setPlaceholderText(self._chat_placeholder_for_mode())
        self._chat_input.setFont(QFont("Segoe UI", 11))
        self._chat_input.setStyleSheet(
            """
            QLineEdit {
                background: transparent;
                border: none;
                color: white;
                padding: 0px;
            }
            QLineEdit::placeholder {
                color: rgba(255, 255, 255, 130);
            }
            """
        )
        self._chat_input.returnPressed.connect(self._on_chat_input_submit)
        self._chat_input.installEventFilter(self)
        self._chat_input.hide()

        self.wisps = [self._spawn_wisp() for _ in range(12)]

    def _spawn_wisp(self, target: dict | None = None) -> dict:
        cx, cy = self.ORB_CX, self.ORB_CY
        base_radius = self.ORB_BASE_R
        a = random.random() * math.pi * 2
        r = math.sqrt(random.random()) * base_radius * 0.6
        max_life = 1.6 + random.random() * 1.6
        w = {
            'x': cx + math.cos(a) * r,
            'y': cy + math.sin(a) * r,
            'vx': 0.0,
            'vy': 0.0,
            'life': max_life,
            'max_life': max_life,
            'hue': random.randint(0, 2),
            'size': base_radius * (0.32 + random.random() * 0.22)
        }
        if target is not None:
            target.update(w)
            return target
        return w

    def set_state(self, state: str) -> None:
        normalized = state.lower()
        if normalized not in {self.IDLE, self.LISTENING, self.THINKING, self.SPEAKING}:
            raise ValueError(f"Unsupported widget state: {state}")
        if normalized == self.state:
            return
        self.state = normalized
        self.state_changed.emit(self.state)
        self.update()

    def set_mode(self, mode: str) -> None:
        normalized = mode.lower()
        if normalized not in {self.HELP, self.ACTIVE}:
            raise ValueError(f"Unsupported widget mode: {mode}")
        if normalized == self.mode:
            return
        self.mode = normalized
        self._chat_input.setPlaceholderText(self._chat_placeholder_for_mode())
        self.mode_changed.emit(self.mode)
        self.update()

    def _chat_placeholder_for_mode(self) -> str:
        if self.mode == self.HELP:
            return self.HELP_CHAT_PLACEHOLDER
        return self.ACTIVE_CHAT_PLACEHOLDER

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
        left = int(self.ORB_CX_OPEN + self.ORB_BASE_R + 8)
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

    def toggle_mode(self) -> None:
        order = self.MODE_ORDER
        idx = order.index(self.mode) if self.mode in order else 0
        self.set_mode(order[(idx + 1) % len(order)])

    def toggle_muted(self) -> None:
        self.set_muted(not self.muted)

    def _spawn_ripple(self, point: QPointF) -> None:
        self._ripples.append({'cx': point.x(), 'cy': point.y(), 't': 0.0, 'max_t': 0.7})
        if len(self._ripples) > 5:
            self._ripples = self._ripples[-5:]

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
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
        self._update_hover(event.position())
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
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
                        self._ripples.clear()
                        self._hold_timer.stop()
                    self._was_dragging = True
            self._last_window_pos = new_top_left
            self.move(new_top_left)
            event.accept()
            return
        self._update_hover(event.position())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._hold_timer.stop()
            if self._voice_hold_active:
                self._voice_hold_active = False
                self.voice_hold_finished.emit()
            elif getattr(self, '_click_pos', None) is not None and not self._was_dragging:
                dist = (event.globalPosition() - self._click_pos).manhattanLength()
                if dist < 5:
                    if self._click_zone == self.ZONE_LEFT:
                        self.mode_toggle_requested.emit()
                    elif self._click_zone == self.ZONE_RIGHT:
                        self.toggle_muted()
                    elif self._click_zone == self.ZONE_ORB:
                        self.chat_requested.emit()
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
            self._ripples.clear()
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

        for rip in self._ripples:
            rip['t'] += dt
        self._ripples = [rip for rip in self._ripples if rip['t'] < rip['max_t']]

        animating = (
            self.state != self.IDLE
            or self._ripples
            or abs(self._press_amt - self._press_target) > 0.005
            or abs(self._hover_amt - self._hover_target) > 0.005
            or abs(self._open_amt - self._open_target) > 0.005
        )
        target_interval = self._frame_interval_active if animating else self._frame_interval_idle
        if self._frame_timer.interval() != target_interval:
            self._frame_timer.setInterval(target_interval)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        self._paint_pill_background(painter, self._hover_amt, self._open_amt)

        cx_w = self._effective_orb_x()
        cy_w = self.ORB_CY
        base_radius = self.ORB_BASE_R + (self.ORB_BASE_R_OPEN - self.ORB_BASE_R) * self._open_amt
        cx = cx_w
        cy = cy_w

        if self.mode == self.HELP:
            col_hi = QColor(230, 245, 255, 255)
            col_mid = QColor(165, 210, 255, 250)
            col_base = QColor(120, 185, 245, 250)
        else:
            col_hi = QColor(210, 250, 240, 255)
            col_mid = QColor(130, 225, 205, 250)
            col_base = QColor(75, 200, 180, 250)

        col_white = QColor(230, 245, 255)
        col_deep = mix_color(col_base, QColor(0, 10, 40), 0.55)

        level = 0.0
        bass = 0.0
        treble = 0.0
        active = False

        if self.state in {self.LISTENING, self.THINKING, self.SPEAKING}:
            active = True
            if self.state == self.SPEAKING:
                env = noise2(0.1, t * 1.1) * 0.5 + noise2(0.3, t * 3.4) * 0.3 + noise2(0.7, t * 8.6) * 0.2
                level = max(0.0, min(1.0, 0.4 + env * 0.45))
                bass = max(0.0, min(1.0, 0.35 + noise2(1.1, t * 2.2) * 0.45))
                treble = max(0.0, min(1.0, 0.30 + noise2(2.3, t * 7.1) * 0.45))
            elif self.state == self.LISTENING:
                level = max(0.0, 0.2 + 0.1 * math.sin(t * 3.0))
                bass = 0.1
                treble = 0.0
            elif self.state == self.THINKING:
                level = max(0.0, 0.15 + 0.05 * math.sin(t * 8.0))
                bass = 0.0
                treble = max(0.0, 0.2 + 0.1 * math.sin(t * 12.0))

        self.level_smoothed = self.level_smoothed * 0.82 + level * 0.18
        lv = self.level_smoothed

        idle_breath = math.sin(t * 0.42) * 0.012
        active_breath = lv * 0.045
        r = base_radius * (1 + idle_breath + active_breath)

        if active and lv > 0.05:
            painter.save()
            halo_r = r * 1.6
            halo_grad = QRadialGradient(cx, cy, halo_r)
            halo_alpha = int(min(0.18, lv * 0.30) * 255)
            halo_grad.setColorAt(0.0, QColor(col_hi.red(), col_hi.green(), col_hi.blue(), 0))
            halo_grad.setColorAt(r / halo_r, QColor(col_hi.red(), col_hi.green(), col_hi.blue(), halo_alpha))
            halo_grad.setColorAt(1.0, QColor(col_hi.red(), col_hi.green(), col_hi.blue(), 0))
            painter.setBrush(halo_grad)
            painter.drawEllipse(QRectF(cx - halo_r, cy - halo_r, halo_r * 2, halo_r * 2))
            painter.restore()

        flow_speed = (38 + bass * 95) if active else 14
        for w in self.wisps:
            eps = 0.55
            nx = w['x'] / base_radius
            ny = w['y'] / base_radius
            tn = t * 0.18
            dnx = noise2(nx, ny + eps + tn) - noise2(nx, ny - eps + tn)
            dny = noise2(nx + eps, ny + tn) - noise2(nx - eps, ny + tn)
            w['vx'] = w['vx'] * 0.90 + dnx * flow_speed * dt * 8

            w['vy'] = w['vy'] * 0.90 + (-dny) * flow_speed * dt * 8

            dxc = w['x'] - cx_w
            dyc = w['y'] - cy_w
            dist = math.sqrt(dxc * dxc + dyc * dyc) or 1
            if dist > base_radius * 0.7:
                pull = (dist - base_radius * 0.7) * 5
                w['vx'] -= (dxc / dist) * pull * dt
                w['vy'] -= (dyc / dist) * pull * dt

            w['x'] += w['vx'] * dt
            w['y'] += w['vy'] * dt
            w['life'] -= dt * (0.55 if active else 0.30)
            if w['life'] <= 0:
                self._spawn_wisp(w)

        painter.save()
        wobble_amp = 0.018
        bass_amp = 0.028
        poly = QPolygonF()
        segments = 64
        ax_x, ax_y = self._press_axis

        for i in range(segments + 1):
            ang = (i / segments) * math.pi * 2
            cos_ang = math.cos(ang)
            sin_ang = math.sin(ang)
            wob = wobble(ang, t) * wobble_amp + bass * bass_amp
            rr = r * (1 + wob)

            if self._press_amt > 0.001:
                cos_axis = cos_ang * ax_x + sin_ang * ax_y
                squash = 1 - self._press_amt * 0.25 * (cos_axis * cos_axis)
                bulge = 1 + self._press_amt * 0.18 * (1 - cos_axis * cos_axis)
                rr *= squash * bulge

            if self._ripples:
                ripple_term = 0.0
                for rip in self._ripples:
                    progress = rip['t'] / rip['max_t']
                    age = 1.0 - progress
                    band = math.exp(-((rr / r - progress * 1.15) ** 2) * 28.0)
                    ripple_term += 0.06 * age * band
                rr *= 1 + ripple_term

            px = cx + cos_ang * rr
            py = cy + sin_ang * rr
            poly.append(QPointF(px, py))

        path = QPainterPath()
        path.addPolygon(poly)
        painter.setClipPath(path)

        base_shifted = mix_color(col_base, QColor(0, 0, 0), bass * 0.25)
        body = QLinearGradient(cx, cy - r, cx, cy + r)
        body.setColorAt(0.0, col_white)
        body.setColorAt(0.28, col_hi)
        body.setColorAt(0.65, col_mid)
        body.setColorAt(1.0, base_shifted)
        painter.setBrush(body)
        painter.drawRect(QRectF(cx - r - 6, cy - r - 6, r * 2 + 12, r * 2 + 12))

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for w in self.wisps:
            life_fade = max(0.0, min(1.0, min(w['life'], w['max_life'] - w['life']) * 1.6))
            treble_pulse = 1 + treble * 0.22
            wr = w['size'] * (1 + lv * 0.32) * treble_pulse

            c = col_hi if w['hue'] == 0 else col_mid if w['hue'] == 1 else col_white
            cf = QColor(
                int(c.red() * life_fade),
                int(c.green() * life_fade),
                int(c.blue() * life_fade),
            )
            cf_mid = QColor(
                int(cf.red() * 0.45),
                int(cf.green() * 0.45),
                int(cf.blue() * 0.45),
            )

            grad = QRadialGradient(w['x'], w['y'], wr)
            grad.setColorAt(0.0, cf)
            grad.setColorAt(0.55, cf_mid)
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setBrush(grad)
            painter.drawEllipse(QRectF(w['x'] - wr, w['y'] - wr, wr * 2, wr * 2))

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        sx_off = (noise2(0.7, t * 0.08) - 0.5) * r * 0.65
        sy_off = (noise2(1.3, t * 0.08) - 0.5) * r * 0.28
        spec_cx = cx + sx_off
        spec_cy = cy - r * 0.4 + sy_off
        spec = QRadialGradient(spec_cx, spec_cy, r * 0.7)
        spec.setColorAt(0.0, QColor(255, 255, 255, int(0.85 * 255)))
        spec.setColorAt(0.35, QColor(255, 255, 255, int(0.18 * 255)))
        spec.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(spec)
        painter.drawRect(QRectF(cx - r - 6, cy - r - 6, r * 2 + 12, r * 2 + 12))

        sheen = QLinearGradient(cx, cy - r, cx, cy - r * 0.4)
        sheen.setColorAt(0.0, QColor(255, 255, 255, int(0.55 * 255)))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(sheen)
        painter.drawRect(QRectF(cx - r - 6, cy - r - 6, r * 2 + 12, r * 2 + 12))

        painter.restore()

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1.3))
        painter.drawPath(path)

        if treble > 0.05:
            dot_count = 6
            for i in range(dot_count):
                phase_ang = (i / dot_count) * math.pi * 2 + t * 0.3
                jitter = (noise2(i * 1.7, t * 4) - 0.5) * 0.25
                ang = phase_ang + jitter
                dx = cx + math.cos(ang) * r * 0.97
                dy = cy + math.sin(ang) * r * 0.97
                dr = 1.5 + treble * 2.5
                dot_alpha = treble * 0.55 * (0.6 + 0.4 * noise2(i * 3.1, t * 6))

                dot_grad = QRadialGradient(dx, dy, dr * 3)
                dot_grad.setColorAt(0.0, QColor(255, 255, 255, int(dot_alpha * 255)))
                dot_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
                painter.setBrush(dot_grad)
                painter.drawEllipse(QRectF(dx - dr * 3, dy - dr * 3, dr * 6, dr * 6))

        self._paint_left_icon(painter, self._hover_amt)
        self._paint_right_icon(painter, self._hover_amt)

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

        painter.setBrush(QColor(225, 228, 235, 255))
        painter.drawRoundedRect(rect, radius, radius)

        body = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        body.setColorAt(0.0, QColor(245, 247, 252, 255))
        body.setColorAt(0.5, QColor(225, 228, 235, 255))
        body.setColorAt(1.0, QColor(200, 205, 215, 255))
        painter.setBrush(body)
        painter.drawRoundedRect(rect, radius, radius)

        gloss = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.center().y()))
        gloss.setColorAt(0.0, QColor(255, 255, 255, 110))
        gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(gloss)
        painter.drawRoundedRect(rect, radius, radius)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 170), 1.0))
        inner = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(inner, radius - 0.5, radius - 0.5)
        painter.restore()

    def _paint_left_icon(self, painter: QPainter, hover_amt: float) -> None:
        icon_alpha = max(0.0, min(1.0, (hover_amt - 0.5) / 0.45)) * (1.0 - self._open_amt)
        if icon_alpha < 0.01:
            return
        cx, cy = self.LEFT_CX, self.LEFT_CY
        if self.mode == self.ACTIVE:
            colors = (
                QColor(120, 220, 200, 235),
                QColor(160, 240, 220, 235),
                QColor(90, 200, 180, 235),
            )
        else:
            colors = (
                QColor(255, 255, 255, 235),
                QColor(235, 240, 248, 220),
                QColor(200, 215, 230, 210),
            )

        petal_r = 4.2
        offset = 3.6
        centers = (
            QPointF(cx, cy - offset),
            QPointF(cx - offset * 0.92, cy + offset * 0.62),
            QPointF(cx + offset * 0.92, cy + offset * 0.62),
        )

        painter.save()
        painter.setOpacity(icon_alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        for color, center in zip(colors, centers):
            grad = QRadialGradient(center.x() - 0.8, center.y() - 0.8, petal_r * 1.4)
            grad.setColorAt(0.0, QColor(255, 255, 255, 200))
            grad.setColorAt(0.45, color)
            grad.setColorAt(1.0, QColor(
                max(0, color.red() - 60),
                max(0, color.green() - 60),
                max(0, color.blue() - 60),
                color.alpha(),
            ))
            painter.setBrush(grad)
            painter.drawEllipse(center, petal_r, petal_r)
        painter.restore()

    def _paint_right_icon(self, painter: QPainter, hover_amt: float) -> None:
        icon_alpha = max(0.0, min(1.0, (hover_amt - 0.5) / 0.45)) * (1.0 - self._open_amt)
        if icon_alpha < 0.01:
            return
        cx, cy = self.RIGHT_CX, self.RIGHT_CY
        body_w = 11.0
        body_h = 8.4
        body_rect = QRectF(cx - body_w / 2 - 1.6, cy - body_h / 2, body_w, body_h)

        painter.save()
        painter.setOpacity(icon_alpha)
        painter.setPen(Qt.PenStyle.NoPen)

        body_grad = QLinearGradient(body_rect.topLeft(), body_rect.bottomLeft())
        body_grad.setColorAt(0.0, QColor(195, 200, 215, 235))
        body_grad.setColorAt(1.0, QColor(140, 150, 170, 235))
        painter.setBrush(body_grad)
        painter.drawRoundedRect(body_rect, 1.8, 1.8)

        lens = QPainterPath()
        lens.moveTo(body_rect.right() - 0.5, cy - 2.6)
        lens.lineTo(body_rect.right() + 4.2, cy - 4.0)
        lens.lineTo(body_rect.right() + 4.2, cy + 4.0)
        lens.lineTo(body_rect.right() - 0.5, cy + 2.6)
        lens.closeSubpath()
        painter.drawPath(lens)

        painter.setBrush(QColor(255, 255, 255, 130))
        highlight = QRectF(body_rect.left() + 1.2, body_rect.top() + 1.0, body_rect.width() - 2.4, 1.2)
        painter.drawRoundedRect(highlight, 0.6, 0.6)

        if self.muted:
            painter.setPen(QPen(QColor(230, 70, 70, 235), 1.6))
            painter.drawLine(int(cx - 9), int(cy + 7), int(cx + 9), int(cy - 7))
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
        painter.setPen(QColor(240, 248, 255, 230))
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter), elided)
        painter.restore()

    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        menu.setStyleSheet(
            """
            QMenu {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255, 255, 255, 20), stop:1 rgba(255, 255, 255, 5));
                border-top: 1px solid rgba(255, 255, 255, 60);
                border-left: 1px solid rgba(255, 255, 255, 40);
                border-right: 1px solid rgba(255, 255, 255, 10);
                border-bottom: 1px solid rgba(255, 255, 255, 10);
                padding: 6px;
                border-radius: 12px;
            }
            QMenu::item {
                color: white;
                padding: 6px 18px;
                border-radius: 6px;
                background-color: transparent;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 30);
                border: 1px solid rgba(255, 255, 255, 50);
            }
            """
        )

        mode_labels = (
            (self.HELP, "Help Mode"),
            (self.ACTIVE, "Active Mode"),
        )
        for value, label in mode_labels:
            marker = "•  " if self.mode == value else "    "
            action = QAction(marker + label, self)
            action.triggered.connect(
                lambda checked=False, target=value: self.mode_select_requested.emit(target)
            )
            menu.addAction(action)

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
        self._font = QFont("Segoe UI", 9)
        self._padding_x = 16
        self._padding_y = 6
        self.setFixedHeight(26)
        self.setFixedWidth(220)
        self.hide()

    def set_text(self, text: str) -> None:
        text = (text or "").strip()
        if text == self._text:
            if not text:
                self.hide()
            return
        self._text = text
        if not text:
            self.hide()
            return
        metrics = QFontMetrics(self._font)
        text_w = metrics.horizontalAdvance(text)
        width = max(140, min(420, text_w + 2 * self._padding_x))
        self.setFixedWidth(width)
        self.update()
        if not self.isVisible():
            self.show()

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
        radius = rect.height() / 2.0

        shadow = QRectF(rect).adjusted(0.0, 1.5, 0.0, 3.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.drawRoundedRect(shadow, radius, radius)

        painter.setBrush(QColor(248, 249, 252, 245))
        painter.drawRoundedRect(rect, radius, radius)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 30), 1.0))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)

        if self._text:
            painter.setFont(self._font)
            painter.setPen(QColor(60, 70, 85, 240))
            metrics = QFontMetrics(self._font)
            elided = metrics.elidedText(
                self._text,
                Qt.TextElideMode.ElideRight,
                int(rect.width()) - 2 * self._padding_x,
            )
            painter.drawText(
                rect,
                int(Qt.AlignmentFlag.AlignCenter),
                elided,
            )


class ChatInputPopup(QWidget):
    submitted = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(360, 56)

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(FloatingCircle.HELP_CHAT_PLACEHOLDER)
        self._edit.setStyleSheet(
            """
            QLineEdit {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255, 255, 255, 20), stop:1 rgba(255, 255, 255, 5));
                border-top: 1px solid rgba(255, 255, 255, 60);
                border-left: 1px solid rgba(255, 255, 255, 40);
                border-right: 1px solid rgba(255, 255, 255, 10);
                border-bottom: 1px solid rgba(255, 255, 255, 10);
                border-radius: 12px;
                padding: 8px 14px;
                color: white;
                font-family: 'Segoe UI';
                font-size: 11pt;
            }
            QLineEdit:focus {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255, 255, 255, 40), stop:1 rgba(255, 255, 255, 10));
                border-top: 1px solid rgba(255, 255, 255, 80);
                border-left: 1px solid rgba(255, 255, 255, 60);
            }
            """
        )
        self._edit.returnPressed.connect(self._submit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._edit)

    def open_at(self, global_pos: QPoint) -> None:
        self.move(global_pos)
        self._edit.clear()
        self.show()
        self.raise_()
        self.activateWindow()
        self._edit.setFocus(Qt.FocusReason.PopupFocusReason)

    def _submit(self) -> None:
        text = self._edit.text().strip()
        self.hide()
        if text:
            self.submitted.emit(text)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)


def _run_demo() -> int:
    app = QApplication(sys.argv)
    widget = FloatingCircle()
    widget.mode_toggle_requested.connect(widget.toggle_mode)
    widget.move(80, 80)
    widget.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(_run_demo())
